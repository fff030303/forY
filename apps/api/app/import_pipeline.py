import json
import sqlite3
from collections import Counter
from datetime import date
from typing import Any, Optional

from fastapi import HTTPException

from .database import connect, row_to_dict
from .deepseek_service import deepseek_json
from .services import fingerprint, new_id, now
from .wechat_connector import fetch_private_history_page


MAX_CHUNK_MESSAGES = 200
MAX_CHUNK_TOKENS = 6000
CHUNK_OVERLAP_MESSAGES = 6
CONVERSATION_GAP_SECONDS = 6 * 60 * 60


def create_import_job(
    project_id: str,
    source_chat: str,
    self_speaker: str,
    since: Optional[date],
    until: Optional[date],
    page_size: int,
    analyze: bool,
) -> dict[str, Any]:
    job_id = new_id()
    timestamp = now()
    with connect() as db:
        db.execute(
            """INSERT INTO import_jobs (
            id, project_id, source_type, source_chat, self_speaker,
            since_date, until_date, status, page_size, analyze_requested,
            created_at, updated_at
            ) VALUES (?, ?, 'wechat', ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
            (
                job_id,
                project_id,
                source_chat,
                self_speaker,
                since.isoformat() if since else None,
                until.isoformat() if until else None,
                page_size,
                int(analyze),
                timestamp,
                timestamp,
            ),
        )
    return get_import_job(project_id, job_id)


def get_import_job(project_id: str, job_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM import_jobs WHERE id = ? AND project_id = ?",
            (job_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        participants = [
            {"name": item["speaker"], "message_count": item["message_count"]}
            for item in db.execute(
                """SELECT speaker, COUNT(*) AS message_count
                FROM raw_messages WHERE import_job_id = ?
                GROUP BY speaker ORDER BY message_count DESC""",
                (job_id,),
            ).fetchall()
        ]
    result = row_to_dict(row) or {}
    result["participants"] = participants
    result["can_build"] = len(participants) == 2
    return result


def list_import_jobs(project_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        ids = [
            row["id"]
            for row in db.execute(
                """SELECT id FROM import_jobs WHERE project_id = ?
                ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        ]
    return [get_import_job(project_id, job_id) for job_id in ids]


def prepare_import_job_resume(project_id: str, job_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT status FROM import_jobs WHERE id = ? AND project_id = ?",
            (job_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        if row["status"] not in ("failed", "queued"):
            raise HTTPException(status_code=409, detail="当前任务不能续传")
        db.execute(
            """UPDATE import_jobs SET status = 'queued', error = NULL,
            updated_at = ? WHERE id = ?""",
            (now(), job_id),
        )
    return get_import_job(project_id, job_id)


def prepare_import_job_reanalysis(
    project_id: str,
    job_id: str,
) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            """SELECT status, import_complete FROM import_jobs
            WHERE id = ? AND project_id = ?""",
            (job_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        if row["status"] != "completed" or not row["import_complete"]:
            raise HTTPException(status_code=409, detail="只有已完成的导入可以重新分析")
        db.execute(
            """UPDATE analysis_chunks SET status = 'pending',
            analysis_json = NULL, prompt_tokens = 0, completion_tokens = 0,
            cache_hit_tokens = 0, cache_miss_tokens = 0, error = NULL,
            updated_at = ? WHERE import_job_id = ?""",
            (now(), job_id),
        )
        db.execute(
            "UPDATE raw_messages SET is_analyzed = 0 WHERE import_job_id = ?",
            (job_id,),
        )
        db.execute(
            """UPDATE import_jobs SET status = 'queued', analyze_requested = 1,
            analyzed_chunk_count = 0, summary_json = NULL, error = NULL,
            updated_at = ? WHERE id = ?""",
            (now(), job_id),
        )
    return get_import_job(project_id, job_id)


def _update_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = now()
    columns = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(
            f"UPDATE import_jobs SET {columns} WHERE id = ?",
            [*values.values(), job_id],
        )


def _job_row(job_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return dict(row)


def _persist_page(
    job: dict[str, Any],
    messages: list[dict[str, Any]],
    offset: int,
) -> tuple[int, int]:
    inserted = 0
    duplicates = 0
    timestamp = now()
    with connect() as db:
        for index, message in enumerate(messages):
            normalized = {
                "speaker": message["speaker"],
                "text": message["normalized_text"],
                "sent_at": message.get("sent_at") or message.get("timestamp"),
            }
            message_fingerprint = fingerprint(normalized)
            raw_id = new_id()
            try:
                db.execute(
                    """INSERT INTO raw_messages (
                    id, project_id, import_job_id, source_message_id,
                    source_chat, speaker, sent_at, source_timestamp,
                    raw_text, normalized_text, fingerprint, source_offset,
                    created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        raw_id,
                        job["project_id"],
                        job["id"],
                        message.get("source_message_id"),
                        job["source_chat"],
                        message["speaker"],
                        message.get("sent_at"),
                        message.get("timestamp"),
                        message["raw_text"],
                        message["normalized_text"],
                        message_fingerprint,
                        offset + index,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                duplicates += 1
                continue
            inserted += 1
            db.execute(
                """INSERT OR IGNORE INTO source_messages (
                id, project_id, fingerprint, speaker, sent_at,
                normalized_text, source_line, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    raw_id,
                    job["project_id"],
                    message_fingerprint,
                    message["speaker"],
                    message.get("sent_at") or message.get("timestamp"),
                    message["normalized_text"],
                    offset + index + 1,
                    timestamp,
                ),
            )
    return inserted, duplicates


def _message_token_estimate(message: dict[str, Any]) -> int:
    return max(1, len(message["normalized_text"]) + len(message["speaker"]) + 8)


def _is_conversation_boundary(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    previous_timestamp = previous.get("source_timestamp")
    current_timestamp = current.get("source_timestamp")
    if isinstance(previous_timestamp, int) and isinstance(current_timestamp, int):
        if current_timestamp - previous_timestamp > CONVERSATION_GAP_SECONDS:
            return True
    previous_date = str(previous.get("sent_at") or "")[:10]
    current_date = str(current.get("sent_at") or "")[:10]
    return bool(previous_date and current_date and previous_date != current_date)


def _create_chunks(job: dict[str, Any]) -> int:
    with connect() as db:
        existing = db.execute(
            "SELECT COUNT(*) FROM analysis_chunks WHERE import_job_id = ?",
            (job["id"],),
        ).fetchone()[0]
        if existing:
            return int(existing)
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM raw_messages WHERE import_job_id = ?
                ORDER BY source_timestamp, source_offset""",
                (job["id"],),
            ).fetchall()
        ]
        chunk_index = 0
        start = 0
        while start < len(rows):
            end = start
            token_estimate = 0
            broke_on_conversation_boundary = False
            while end < len(rows):
                if end > start and _is_conversation_boundary(rows[end - 1], rows[end]):
                    broke_on_conversation_boundary = True
                    break
                next_tokens = _message_token_estimate(rows[end])
                if end > start and (
                    end - start >= MAX_CHUNK_MESSAGES
                    or token_estimate + next_tokens > MAX_CHUNK_TOKENS
                ):
                    break
                token_estimate += next_tokens
                end += 1
            if end == start:
                token_estimate = _message_token_estimate(rows[end])
                end += 1
            chunk_rows = rows[start:end]
            timestamp = now()
            db.execute(
                """INSERT INTO analysis_chunks (
                id, project_id, import_job_id, chunk_index, status,
                message_ids_json, message_count, token_estimate,
                started_at, ended_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    job["project_id"],
                    job["id"],
                    chunk_index,
                    json.dumps([row["id"] for row in chunk_rows]),
                    len(chunk_rows),
                    token_estimate,
                    chunk_rows[0].get("sent_at"),
                    chunk_rows[-1].get("sent_at"),
                    timestamp,
                    timestamp,
                ),
            )
            chunk_index += 1
            if end >= len(rows):
                break
            if broke_on_conversation_boundary:
                start = end
            else:
                start = max(start + 1, end - CHUNK_OVERLAP_MESSAGES)
    return chunk_index


def _attach_message_ids(value: Any, message_ids: list[str]) -> Any:
    if isinstance(value, list):
        return [_attach_message_ids(item, message_ids) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _attach_message_ids(item, message_ids)
        for key, item in value.items()
    }
    for key, item in value.items():
        if key.endswith("_indexes") and isinstance(item, list):
            result[key.removesuffix("_indexes") + "_message_ids"] = [
                message_ids[index - 1]
                for index in item
                if isinstance(index, int) and 1 <= index <= len(message_ids)
            ]
        if key.endswith("_index") and isinstance(item, int):
            message_key = key.removesuffix("_index") + "_message_id"
            result[message_key] = (
                message_ids[item - 1] if 1 <= item <= len(message_ids) else None
            )
    return result


CHUNK_ANALYSIS_PROMPT = """你是情绪与人格模式分析器。用户会提供一个双人聊天片段，
每条消息都有 M 序号。请进行语义分段，并只输出一个合法 JSON 对象。
重点分析目标人物的稳定性格、情绪触发与调节、关系互动、冲突反应和内在需求，
而不是预测下一句回复。必须区分短期情绪状态和稳定人格；不得把单次行为直接判断为
稳定特征。每项结论必须引用 evidence_indexes，并尽可能提供反例。
事件和长期记忆必须遵守“原子事实”原则：一项只描述一件可独立验证的事情。同一
时间连续出现的两件事，不代表前者造成后者；若原文没有明确说“因为、所以、导致、
害得、造成”等因果关系，禁止自行添加因果连接词，必须拆成两项。多件倒霉事连在
一起诉说时，事实分别记录，“诉说倒霉、烦躁或无奈”先写入 topics 的情绪语境；
只有多个独立场景反复出现时才能归入 emotional_patterns，不能把一次情绪或情绪
语境写成事实间的因果。

JSON 格式：
{
  "topics": [{"title": "", "start_index": 1, "end_index": 2, "summary": ""}],
  "emotion_observations": [{"message_index": 1, "emotion": "", "valence": 0.0, "arousal": 0.0, "intensity": 0.0, "target": "", "expression_mode": "", "social_function": "", "cause_indexes": [], "confidence": 0.0}],
  "emotional_episodes": [{"title": "", "start_index": 1, "end_index": 2, "facts": [], "initial_emotion": "", "peak_emotion": "", "expression_mode": "", "coping": "", "social_function": "", "relationship_signal": "", "evidence_indexes": []}],
  "personality_dimensions": [{"name": "", "tendency": "", "behavior_pattern": "", "confidence": 0.0, "evidence_indexes": [1], "counterexample_indexes": []}],
  "emotional_patterns": [{"emotion": "", "triggers": "", "expression": "", "regulation": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "relationship_patterns": [{"name": "", "description": "", "toward_user": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "conflict_patterns": [{"trigger": "", "reaction": "", "repair": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "needs_and_boundaries": [{"type": "need", "description": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "temporal_changes": [{"earlier": "", "recent": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "style_traits": [{"name": "", "value": "", "confidence": 0.0, "evidence_indexes": [1]}],
  "catchphrases": [{"text": "", "evidence_indexes": [1]}],
  "events": [{"description": "", "time": "", "evidence_indexes": [1]}],
  "memory_candidates": [{"content": "", "event_date": "YYYY-MM-DD", "importance": 0.0, "evidence_indexes": [1]}],
  "reply_examples": [{"context_index": 1, "reply_index": 2, "quality_reason": ""}]
}
confidence 和 importance 必须在 0 到 1 之间。personality_dimensions 必须至少有
2 条证据才可输出；证据不足时宁可留空。reply_examples 仅作为后台语气素材，最多
2 项。emotion_observations 是单条状态，emotional_episodes 是一次完整情绪过程，
personality_dimensions 和 emotional_patterns 只能描述跨场景稳定规律。只分析
指定的目标人物，不要对当前用户做人格诊断。"""


STABLE_ANALYSIS_CATEGORIES = {
    "personality_dimensions",
    "emotional_patterns",
    "relationship_patterns",
    "conflict_patterns",
    "needs_and_boundaries",
    "style_traits",
}


def _annotate_scene_dates(
    analysis: dict[str, Any],
    rows_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for category in STABLE_ANALYSIS_CATEGORIES:
        values = analysis.get(category)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            dates = list(dict.fromkeys(
                str(rows_by_id[message_id].get("sent_at") or "")[:10]
                for message_id in item.get("evidence_message_ids", [])
                if message_id in rows_by_id
                and str(rows_by_id[message_id].get("sent_at") or "")[:10]
            ))
            item["scene_dates"] = dates
            item["scene_count"] = len(dates)
    return analysis


def _analyze_chunk(job: dict[str, Any], chunk: dict[str, Any]) -> None:
    message_ids = chunk["message_ids"]
    placeholders = ",".join("?" for _ in message_ids)
    with connect() as db:
        rows_by_id = {
            row["id"]: dict(row)
            for row in db.execute(
                f"SELECT * FROM raw_messages WHERE id IN ({placeholders})",
                message_ids,
            ).fetchall()
        }
    rows = [rows_by_id[item] for item in message_ids if item in rows_by_id]
    transcript = "\n".join(
        f"[M{index:04d}][{row.get('sent_at') or ''}][{row['speaker']}] "
        f"{row['normalized_text']}"
        for index, row in enumerate(rows, start=1)
    )
    payload, usage = deepseek_json(
        CHUNK_ANALYSIS_PROMPT,
        f"目标人物：{job['source_chat']}\n当前用户：{job['self_speaker']}\n\n{transcript}",
    )
    analysis = _annotate_scene_dates(
        _attach_message_ids(payload, message_ids),
        rows_by_id,
    )
    timestamp = now()
    with connect() as db:
        db.execute(
            """UPDATE analysis_chunks SET status = 'analyzed',
            analysis_json = ?, prompt_tokens = ?, completion_tokens = ?,
            cache_hit_tokens = ?, cache_miss_tokens = ?, error = NULL,
            updated_at = ? WHERE id = ?""",
            (
                json.dumps(analysis, ensure_ascii=False),
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["cache_hit_tokens"],
                usage["cache_miss_tokens"],
                timestamp,
                chunk["id"],
            ),
        )
        db.execute(
            f"UPDATE raw_messages SET is_analyzed = 1 WHERE id IN ({placeholders})",
            message_ids,
        )


MERGE_ANALYSIS_PROMPT = """你是情绪与人格分析汇总器。输入是多个聊天片段的 JSON
分析。最终结果的核心是稳定性格、情绪机制和关系模式，不是对答或说话模仿。
请去重、合并，并只输出合法 JSON。矛盾信息必须保留时间或证据差异；少量孤立证据
不能升级为稳定性格。所有结论必须保留原始 evidence_message_ids，但每项最多保留
8 个最有代表性的证据 ID。内容必须精炼，并严格遵守以下数量上限：
personality_dimensions 8 项、emotional_patterns 8 项、relationship_patterns 8 项、
conflict_patterns 6 项、needs_and_boundaries 8 项、temporal_changes 6 项、
style_traits 8 项、catchphrases 12 项、events 24 项、memory_candidates 30 项、
reply_examples 12 项。
style_traits 中名称或含义相近的项目必须合并，name 必须唯一；少于 2 条不同原始
消息支持的结论不得作为稳定 style_trait。不得因单次行为使用“频繁、总是、习惯”
等词。summary 必须是 80～200 字的人格与关系总结，不能描述分析流程、片段数量或
“从若干结果汇总”等技术信息。
events 和 memory_candidates 必须保持原子化：同一天或相邻消息中的独立事件必须
分开，时间相邻不等于存在因果。只有证据原文明确表达因果时，才允许使用“导致、
因为、所以、害得、造成”等词；否则删除因果推断。连续诉说多件倒霉事属于情绪
表达语境，不能把这些事情拼成一条因果事件；只有不同日期或独立场景中反复出现
同类表达，才能汇总为 emotional_patterns。
请额外生成 affect_profile，描述跨场景稳定的情绪基线和反应机制。它不是某一次
情绪的总结，必须由至少两个独立日期或场景支持。无法确认的字段留空，不要猜测。

JSON 格式：
{
  "summary": "",
  "affect_profile": {"baseline": "", "reactivity": "", "expression": "", "regulation": "", "recovery": "", "relationship_orientation": "", "humor_style": "", "confidence": 0.0, "evidence_message_ids": []},
  "emotional_episodes": [{"title": "", "facts": [], "initial_emotion": "", "peak_emotion": "", "expression_mode": "", "coping": "", "social_function": "", "relationship_signal": "", "evidence_message_ids": []}],
  "personality_dimensions": [{"name": "", "tendency": "", "behavior_pattern": "", "confidence": 0.0, "evidence_message_ids": [], "counterexample_message_ids": [], "scene_dates": [], "scene_count": 0}],
  "emotional_patterns": [{"emotion": "", "triggers": "", "expression": "", "regulation": "", "confidence": 0.0, "evidence_message_ids": [], "scene_dates": [], "scene_count": 0}],
  "relationship_patterns": [{"name": "", "description": "", "toward_user": "", "confidence": 0.0, "evidence_message_ids": [], "scene_dates": [], "scene_count": 0}],
  "conflict_patterns": [{"trigger": "", "reaction": "", "repair": "", "confidence": 0.0, "evidence_message_ids": [], "scene_dates": [], "scene_count": 0}],
  "needs_and_boundaries": [{"type": "need", "description": "", "confidence": 0.0, "evidence_message_ids": [], "scene_dates": [], "scene_count": 0}],
  "temporal_changes": [{"earlier": "", "recent": "", "confidence": 0.0, "evidence_message_ids": []}],
  "style_traits": [{"name": "", "value": "", "confidence": 0.0, "evidence_message_ids": []}],
  "catchphrases": [{"text": "", "evidence_message_ids": []}],
  "events": [{"description": "", "time": "", "evidence_message_ids": []}],
  "memory_candidates": [{"content": "", "event_date": "YYYY-MM-DD", "importance": 0.0, "evidence_message_ids": []}],
  "reply_examples": [{"context_message_id": "", "reply_message_id": "", "quality_reason": ""}]
}"""


MERGE_LIMITS = {
    "emotional_episodes": 24,
    "personality_dimensions": 8,
    "emotional_patterns": 8,
    "relationship_patterns": 8,
    "conflict_patterns": 6,
    "needs_and_boundaries": 8,
    "temporal_changes": 6,
    "style_traits": 8,
    "catchphrases": 12,
    "events": 24,
    "memory_candidates": 30,
    "reply_examples": 20,
}
MERGE_IDENTITY_FIELDS = {
    "emotional_episodes": ("title",),
    "personality_dimensions": ("name",),
    "emotional_patterns": ("emotion", "triggers"),
    "relationship_patterns": ("name",),
    "conflict_patterns": ("trigger",),
    "needs_and_boundaries": ("type", "description"),
    "temporal_changes": ("earlier", "recent"),
    "style_traits": ("name",),
    "catchphrases": ("text",),
    "relationship_attitudes": ("description",),
    "events": ("description", "time"),
    "memory_candidates": ("content",),
    "reply_examples": ("context_message_id", "reply_message_id"),
}

STYLE_DEDUP_PROMPT = """你是人格特征去重器。输入是一小组已经有原始消息证据的人格
特征。只输出 JSON 对象 {"style_traits": [...]}。请把名称或含义重叠的特征合并成
一个清晰维度，但不要把机制不同的特征过度合并。目标保留 4～8 项，并尽量区分：
情绪表达方式、语气词/脏话等用词习惯、幽默调侃与自嘲、回复是否简短直接、
表情符号使用、主动互动与关系态度。用词习惯不能并入情绪表达，表情使用不能并入
活泼或幽默。name 必须唯一且各项含义互不重叠；合并 evidence_message_ids 并
去重，每项最多 8 个。少于 2 个不同证据 ID 的项目不要输出。不得添加输入中不存在
的证据 ID。confidence 必须根据证据数量保守估计。"""


def _compact_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "summary": f"从 {len(analyses)} 个聊天分析结果汇总",
    }
    for category, limit in MERGE_LIMITS.items():
        items_by_identity: dict[tuple[str, ...], dict[str, Any]] = {}
        for analysis in analyses:
            values = analysis.get(category)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                identity = tuple(
                    str(value.get(field) or "").strip()
                    for field in MERGE_IDENTITY_FIELDS[category]
                )
                if not any(identity):
                    continue
                item = dict(value)
                evidence = item.get("evidence_message_ids")
                if isinstance(evidence, list):
                    item["evidence_message_ids"] = list(dict.fromkeys(
                        evidence_id for evidence_id in evidence
                        if isinstance(evidence_id, str) and evidence_id
                    ))[:8]
                scene_dates = item.get("scene_dates")
                if isinstance(scene_dates, list):
                    item["scene_dates"] = list(dict.fromkeys(
                        scene_date for scene_date in scene_dates
                        if isinstance(scene_date, str) and scene_date
                    ))
                    item["scene_count"] = len(item["scene_dates"])
                existing = items_by_identity.get(identity)
                if existing:
                    for score in ("confidence", "importance"):
                        if isinstance(item.get(score), (int, float)):
                            existing[score] = max(
                                float(existing.get(score) or 0), float(item[score])
                            )
                    existing_ids = existing.get("evidence_message_ids", [])
                    new_ids = item.get("evidence_message_ids", [])
                    existing["evidence_message_ids"] = list(dict.fromkeys(
                        [*existing_ids, *new_ids]
                    ))[:8]
                    existing_dates = existing.get("scene_dates", [])
                    new_dates = item.get("scene_dates", [])
                    if existing_dates or new_dates:
                        existing["scene_dates"] = list(dict.fromkeys(
                            [*existing_dates, *new_dates]
                        ))
                        existing["scene_count"] = len(existing["scene_dates"])
                    continue
                items_by_identity[identity] = item
        items = list(items_by_identity.values())
        if (
            category in STABLE_ANALYSIS_CATEGORIES
            and len(analyses) > 1
        ):
            items = [
                item for item in items
                if (
                    len(item.get("scene_dates") or []) >= 2
                    if "scene_dates" in item
                    else len(item.get("evidence_message_ids") or []) >= 2
                )
            ]
        items.sort(
            key=lambda item: (
                len(item.get("evidence_message_ids") or []),
                float(item.get("confidence") or item.get("importance") or 0),
            ),
            reverse=True,
        )
        result[category] = items[:limit]
    personality_items = (
        result.get("personality_dimensions")
        or result.get("style_traits", [])
    )
    trait_values = [
        str(
            item.get("tendency")
            or item.get("value")
            or item.get("name")
            or ""
        ).strip()
        for item in personality_items[:4]
    ]
    trait_values = [value for value in trait_values if value]
    if trait_values:
        result["summary"] = (
            "长期聊天记录显示，对方"
            + "；".join(trait_values)
            + "。这些特征来自多条可追溯的原始消息，并会随新记录继续修正。"
        )[:200]
    return result


def _refine_style_traits(traits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(traits) < 2:
        return traits
    allowed_ids = {
        evidence_id
        for trait in traits
        for evidence_id in trait.get("evidence_message_ids", [])
        if isinstance(evidence_id, str) and evidence_id
    }
    try:
        payload, _ = deepseek_json(
            STYLE_DEDUP_PROMPT,
            json.dumps({"style_traits": traits}, ensure_ascii=False),
            max_tokens=4000,
        )
    except HTTPException:
        return traits
    values = payload.get("style_traits")
    if not isinstance(values, list):
        return traits
    refined = []
    names = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "").strip()
        if not name or name in names:
            continue
        evidence = list(dict.fromkeys(
            evidence_id for evidence_id in value.get("evidence_message_ids", [])
            if evidence_id in allowed_ids
        ))[:8]
        if len(evidence) < 2:
            continue
        item = dict(value)
        item["name"] = name
        item["evidence_message_ids"] = evidence
        refined.append(item)
        names.add(name)
    return refined[:8] or traits


def _merge_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    if not analyses:
        return {}
    compact = _compact_analyses(analyses)
    if len(analyses) == 1:
        return compact
    try:
        merged, _ = deepseek_json(
            MERGE_ANALYSIS_PROMPT,
            json.dumps({"analyses": compact}, ensure_ascii=False),
            max_tokens=12000,
        )
    except HTTPException as error:
        if "JSON" in str(error.detail):
            compact["style_traits"] = _refine_style_traits(
                compact["style_traits"]
            )
            return compact
        raise
    normalized = _compact_analyses([merged])
    if isinstance(merged.get("affect_profile"), dict):
        normalized["affect_profile"] = merged["affect_profile"]
    enforce_scene_dates = any(
        "scene_dates" in item
        for category in STABLE_ANALYSIS_CATEGORIES
        for item in compact.get(category, [])
    )
    if enforce_scene_dates:
        for category in STABLE_ANALYSIS_CATEGORIES:
            normalized[category] = [
                item for item in normalized[category]
                if len(item.get("scene_dates") or []) >= 2
            ]
    if merged.get("summary"):
        normalized["summary"] = str(merged["summary"])
    for category in MERGE_LIMITS:
        if not normalized[category]:
            normalized[category] = compact[category]
    normalized["style_traits"] = _refine_style_traits(
        normalized["style_traits"]
    )
    return normalized


def _analyze_job(job: dict[str, Any]) -> dict[str, Any]:
    with connect() as db:
        chunks = [
            row_to_dict(row) or {}
            for row in db.execute(
                """SELECT * FROM analysis_chunks WHERE import_job_id = ?
                ORDER BY chunk_index""",
                (job["id"],),
            ).fetchall()
        ]
    for chunk in chunks:
        if chunk["status"] == "analyzed":
            continue
        try:
            _analyze_chunk(job, chunk)
        except Exception as error:
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            with connect() as db:
                db.execute(
                    """UPDATE analysis_chunks SET status = 'failed', error = ?,
                    updated_at = ? WHERE id = ?""",
                    (str(detail)[:500], now(), chunk["id"]),
                )
            raise
        with connect() as db:
            analyzed_count = db.execute(
                """SELECT COUNT(*) FROM analysis_chunks
                WHERE import_job_id = ? AND status = 'analyzed'""",
                (job["id"],),
            ).fetchone()[0]
        _update_job(job["id"], analyzed_chunk_count=analyzed_count)
    with connect() as db:
        analyses = [
            json.loads(row["analysis_json"])
            for row in db.execute(
                """SELECT analysis_json FROM analysis_chunks
                WHERE project_id = ? AND status = 'analyzed'
                ORDER BY created_at, chunk_index""",
                (job["project_id"],),
            ).fetchall()
            if row["analysis_json"]
        ]
    _update_job(job["id"], status="merging")
    return _merge_analyses(analyses)


def run_import_job(job_id: str) -> None:
    with connect() as db:
        cursor = db.execute(
            """UPDATE import_jobs SET status = 'importing', error = NULL,
            updated_at = ? WHERE id = ? AND status IN ('queued', 'failed')""",
            (now(), job_id),
        )
    if cursor.rowcount == 0:
        return
    try:
        job = _job_row(job_id)
        if not job["import_complete"]:
            since = date.fromisoformat(job["since_date"]) if job["since_date"] else None
            until = date.fromisoformat(job["until_date"]) if job["until_date"] else None
            offset = int(job["next_offset"])
            while True:
                page = fetch_private_history_page(
                    chat=job["source_chat"],
                    self_speaker=job["self_speaker"],
                    since=since,
                    until=until,
                    limit=job["page_size"],
                    offset=offset,
                )
                if not page:
                    _update_job(job_id, import_complete=1)
                    break
                inserted, duplicates = _persist_page(job, page, offset)
                offset += len(page)
                job["imported_count"] += inserted
                job["duplicate_count"] += duplicates
                _update_job(
                    job_id,
                    next_offset=offset,
                    imported_count=job["imported_count"],
                    duplicate_count=job["duplicate_count"],
                )
                if len(page) < job["page_size"]:
                    _update_job(job_id, import_complete=1)
                    break
        _update_job(job_id, status="segmenting")
        job = _job_row(job_id)
        chunk_count = _create_chunks(job)
        _update_job(job_id, chunk_count=chunk_count)
        summary: dict[str, Any] = {}
        if job["analyze_requested"] and chunk_count:
            _update_job(job_id, status="analyzing")
            summary = _analyze_job(job)
        _update_job(
            job_id,
            status="completed",
            summary_json=json.dumps(summary, ensure_ascii=False) if summary else None,
            error=None,
        )
    except Exception as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        _update_job(job_id, status="failed", error=str(detail)[:500])
