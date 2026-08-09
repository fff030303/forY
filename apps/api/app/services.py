import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .database import connect, row_to_dict


WECHAT_LINE = re.compile(
    r"^\s*(?:\[(?P<time>[^\]]+)\]\s*)?(?P<speaker>[^:：]{1,80})[:：]\s*(?P<text>.+?)\s*$"
)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SECRET = re.compile(r"(?i)(?:api[_-]?key|token|password|密码)\s*[:=：]\s*\S+")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def require_project(project_id: str, owner_user_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM projects WHERE id = ? AND owner_user_id = ?",
            (project_id, owner_user_id),
        ).fetchone()
    project = row_to_dict(row)
    if project is None:
        raise HTTPException(status_code=404, detail="人格项目不存在")
    return project


def parse_messages(content: str, format_name: str) -> tuple[list[dict[str, Any]], int]:
    messages: list[dict[str, Any]] = []
    invalid = 0
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if format_name == "jsonl":
            try:
                item = json.loads(line)
                speaker = str(item["speaker"]).strip()
                text = str(item["text"]).strip()
                sent_at = item.get("timestamp") or item.get("sent_at")
            except (json.JSONDecodeError, KeyError, TypeError):
                invalid += 1
                continue
        else:
            match = WECHAT_LINE.match(line)
            if not match:
                invalid += 1
                continue
            speaker = match.group("speaker").strip()
            text = match.group("text").strip()
            sent_at = match.group("time")
        if not speaker or not text:
            invalid += 1
            continue
        messages.append(
            {
                "speaker": speaker,
                "text": text,
                "sent_at": sent_at,
                "source_line": line_number,
            }
        )
    return messages, invalid


def fingerprint(message: dict[str, Any]) -> str:
    value = "|".join(
        [message["speaker"], message["text"], str(message.get("sent_at") or "")]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def privacy_findings(text: str) -> list[str]:
    findings = []
    if PHONE.search(text):
        findings.append("手机号")
    if EMAIL.search(text):
        findings.append("邮箱")
    if SECRET.search(text):
        findings.append("疑似密钥或密码")
    return findings


def import_messages(
    project_id: str, owner_user_id: str, content: str, format_name: str
) -> dict[str, Any]:
    require_project(project_id, owner_user_id)
    parsed, invalid = parse_messages(content, format_name)
    if not parsed:
        raise HTTPException(status_code=422, detail="没有识别到有效消息")
    speakers = Counter(item["speaker"] for item in parsed)
    privacy = Counter()
    duplicate_count = 0
    inserted_count = 0
    with connect() as db:
        for message in parsed:
            privacy.update(privacy_findings(message["text"]))
            try:
                db.execute(
                    """INSERT INTO source_messages (
                    id, project_id, fingerprint, speaker, sent_at,
                    normalized_text, source_line, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id(),
                        project_id,
                        fingerprint(message),
                        message["speaker"],
                        message["sent_at"],
                        message["text"],
                        message["source_line"],
                        now(),
                    ),
                )
                inserted_count += 1
            except Exception as error:
                if "UNIQUE constraint failed" not in str(error):
                    raise
                duplicate_count += 1
    return {
        "detected_format": format_name,
        "parsed_count": len(parsed),
        "inserted_count": inserted_count,
        "duplicate_count": duplicate_count,
        "invalid_count": invalid,
        "participants": [
            {"name": name, "message_count": count}
            for name, count in speakers.most_common()
        ],
        "privacy_findings": [
            {"type": name, "count": count} for name, count in privacy.items()
        ],
        "can_build": len(speakers) == 2,
    }


def _style_traits(texts: list[str]) -> list[dict[str, Any]]:
    total = max(len(texts), 1)
    average_length = round(sum(len(text) for text in texts) / total, 1)
    question_ratio = sum("?" in text or "？" in text for text in texts) / total
    emoji_ratio = sum(bool(re.search(r"[😀-🙏🌀-🫿]", text)) for text in texts) / total
    return [
        {
            "name": "回复长度",
            "value": "简短" if average_length < 18 else "适中" if average_length < 45 else "详细",
            "confidence": min(0.95, 0.55 + total / 100),
            "evidence": f"目标人物平均每条 {average_length} 个字符",
        },
        {
            "name": "提问倾向",
            "value": "常用追问" if question_ratio >= 0.25 else "较少追问",
            "confidence": min(0.9, 0.5 + total / 120),
            "evidence": f"问句占比 {question_ratio:.0%}",
        },
        {
            "name": "表情使用",
            "value": "会用表情" if emoji_ratio >= 0.1 else "很少用表情",
            "confidence": min(0.85, 0.5 + total / 150),
            "evidence": f"含表情消息占比 {emoji_ratio:.0%}",
        },
    ]


def _bounded_score(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _source_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


CAUSAL_WORDS = ("导致", "因为", "所以", "害得", "造成", "致使", "引发")


def _has_unsupported_causal_claim(
    content: str,
    evidence_ids: list[str],
    source_text_by_id: dict[str, str],
) -> bool:
    if not any(word in content for word in CAUSAL_WORDS):
        return False
    evidence_text = "\n".join(
        source_text_by_id.get(message_id, "") for message_id in evidence_ids
    )
    return not any(word in evidence_text for word in CAUSAL_WORDS)


def build_persona(project_id: str, owner_user_id: str) -> dict[str, Any]:
    project = require_project(project_id, owner_user_id)
    if not project["target_speaker"] or not project["user_speaker"]:
        raise HTTPException(status_code=409, detail="请先确认双方身份")
    with connect() as db:
        rows = db.execute(
            """SELECT id, speaker, normalized_text, sent_at
            FROM source_messages WHERE project_id = ? ORDER BY source_line""",
            (project_id,),
        ).fetchall()
        source_text_by_id = {
            row["id"]: row["normalized_text"] for row in rows
        }
        target_rows = [row for row in rows if row["speaker"] == project["target_speaker"]]
        if len(target_rows) < 2:
            raise HTTPException(status_code=409, detail="目标人物有效消息过少")
        version_number = db.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM persona_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        version_id = new_id()
        summary_row = db.execute(
            """SELECT summary_json FROM import_jobs
            WHERE project_id = ? AND status = 'completed'
            AND summary_json IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        ai_summary = json.loads(summary_row["summary_json"]) if summary_row else None
        traits = []
        if isinstance(ai_summary, dict):
            personality_items = (
                ai_summary.get("personality_dimensions")
                or ai_summary.get("style_traits", [])
            )
            for item in personality_items[:8]:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                evidence_ids = _source_ids(item.get("evidence_message_ids"))
                counterexample_ids = _source_ids(
                    item.get("counterexample_message_ids")
                )
                traits.append(
                    {
                        "name": str(item["name"]),
                        "value": str(
                            item.get("tendency")
                            or item.get("value")
                            or item.get("behavior_pattern")
                            or ""
                        ),
                        "confidence": _bounded_score(item.get("confidence"), 0.5),
                        "evidence": f"展示 {len(evidence_ids)} 条代表证据",
                        "source_message_ids": evidence_ids,
                        "counterexample_message_ids": counterexample_ids,
                    }
                )
        if not traits:
            traits = _style_traits([row["normalized_text"] for row in target_rows])
        relationship = {
            "type": project["relationship_type"],
            "target_calls_user": project["user_speaker"],
            "interaction_pattern": "基于真实历史消息与当前用户持续补全",
        }
        if isinstance(ai_summary, dict):
            def stable_items(key: str) -> list[dict[str, Any]]:
                values = ai_summary.get(key, [])
                if not isinstance(values, list):
                    return []
                return [
                    item for item in values
                    if isinstance(item, dict)
                    and (
                        "scene_count" not in item
                        or int(item.get("scene_count") or 0) >= 2
                    )
                ]

            relationship["affect_profile"] = ai_summary.get(
                "affect_profile", {}
            )
            relationship["emotional_episodes"] = ai_summary.get(
                "emotional_episodes", []
            )
            relationship["emotional_patterns"] = stable_items(
                "emotional_patterns"
            )
            relationship["relationship_patterns"] = stable_items(
                "relationship_patterns"
            )
            relationship["conflict_patterns"] = stable_items(
                "conflict_patterns"
            )
            relationship["needs_and_boundaries"] = stable_items(
                "needs_and_boundaries"
            )
            relationship["temporal_changes"] = ai_summary.get(
                "temporal_changes", []
            )
            relationship["events"] = ai_summary.get("events", [])
            relationship["catchphrases"] = ai_summary.get("catchphrases", [])
        version_summary = (
            str(ai_summary.get("summary"))
            if isinstance(ai_summary, dict) and ai_summary.get("summary")
            else f"从 {len(target_rows)} 条目标人物消息生成"
        )
        db.execute(
            """INSERT INTO persona_versions (
            id, project_id, version_number, status, summary,
            traits_json, relationship_json, created_at
            ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?)""",
            (
                version_id,
                project_id,
                version_number,
                version_summary,
                json.dumps(traits, ensure_ascii=False),
                json.dumps(relationship, ensure_ascii=False),
                now(),
            ),
        )
        rows_list = list(rows)
        example_count = 0
        if isinstance(ai_summary, dict):
            for item in ai_summary.get("reply_examples", [])[:12]:
                if not isinstance(item, dict):
                    continue
                context_id = item.get("context_message_id")
                reply_id = item.get("reply_message_id")
                if not isinstance(context_id, str) or not isinstance(reply_id, str):
                    continue
                pair = db.execute(
                    """SELECT id, speaker, normalized_text FROM raw_messages
                    WHERE project_id = ? AND id IN (?, ?)""",
                    (project_id, context_id, reply_id),
                ).fetchall()
                by_id = {row["id"]: row for row in pair}
                context_row = by_id.get(context_id)
                reply_row = by_id.get(reply_id)
                if (
                    not context_row
                    or not reply_row
                    or context_row["speaker"] != project["user_speaker"]
                    or reply_row["speaker"] != project["target_speaker"]
                ):
                    continue
                db.execute(
                    """INSERT INTO dialogue_examples (
                    id, project_id, version_id, context_text, reply_text,
                    source_message_ids_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id(),
                        project_id,
                        version_id,
                        context_row["normalized_text"],
                        reply_row["normalized_text"],
                        json.dumps([context_id, reply_id]),
                        now(),
                    ),
                )
                example_count += 1
        for index, row in enumerate(rows_list):
            if example_count >= 12:
                break
            if row["speaker"] != project["target_speaker"] or index == 0:
                continue
            previous = rows_list[index - 1]
            if previous["speaker"] == project["user_speaker"]:
                db.execute(
                    """INSERT INTO dialogue_examples (
                    id, project_id, version_id, context_text, reply_text,
                    source_message_ids_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id(), project_id, version_id,
                        previous["normalized_text"], row["normalized_text"],
                        json.dumps([previous["id"], row["id"]]), now(),
                    ),
                )
                example_count += 1
        memory_count = 0
        if isinstance(ai_summary, dict):
            for item in ai_summary.get("memory_candidates", [])[:12]:
                if not isinstance(item, dict) or not item.get("content"):
                    continue
                evidence_ids = _source_ids(item.get("evidence_message_ids"))
                if _has_unsupported_causal_claim(
                    str(item["content"]), evidence_ids, source_text_by_id
                ):
                    continue
                db.execute(
                    """INSERT INTO memories (
                    id, project_id, version_id, content, importance, event_date,
                    source_message_ids_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        new_id(),
                        project_id,
                        version_id,
                        str(item["content"]),
                        _bounded_score(item.get("importance"), 0.5),
                        str(item.get("event_date") or "")[:10] or None,
                        json.dumps(evidence_ids),
                        now(),
                        now(),
                    ),
                )
                memory_count += 1
        memory_keywords = ("记得", "一起", "上次", "以前", "明天", "生日", "见面", "喜欢")
        for row in rows_list:
            if memory_count >= 12:
                break
            if any(keyword in row["normalized_text"] for keyword in memory_keywords):
                db.execute(
                    """INSERT INTO memories (
                    id, project_id, version_id, content, importance, event_date,
                    source_message_ids_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        new_id(), project_id, version_id, row["normalized_text"], 0.65,
                        str(row["sent_at"] or "")[:10] or None,
                        json.dumps([row["id"]]), now(), now(),
                    ),
                )
                memory_count += 1
        db.execute(
            """UPDATE projects SET active_version_id = ?, status = 'ready',
            updated_at = ? WHERE id = ?""",
            (version_id, now(), project_id),
        )
    return get_persona(project_id, owner_user_id)


def get_persona(project_id: str, owner_user_id: str) -> dict[str, Any]:
    project = require_project(project_id, owner_user_id)
    if not project["active_version_id"]:
        raise HTTPException(status_code=404, detail="尚未构建人格")
    with connect() as db:
        version = row_to_dict(
            db.execute(
                "SELECT * FROM persona_versions WHERE id = ?",
                (project["active_version_id"],),
            ).fetchone()
        )
        memories = [
            row_to_dict(row)
            for row in db.execute(
                """SELECT * FROM memories WHERE project_id = ?
                AND version_id = ? AND status = 'active'
                ORDER BY event_date DESC, importance DESC, created_at DESC""",
                (project_id, project["active_version_id"]),
            ).fetchall()
        ]
        memory_candidates = [
            row_to_dict(row)
            for row in db.execute(
                """SELECT * FROM memories WHERE project_id = ? AND status = 'candidate'
                ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        ]
        examples = [
            row_to_dict(row)
            for row in db.execute(
                """SELECT * FROM dialogue_examples
                WHERE project_id = ? AND version_id = ?
                ORDER BY created_at LIMIT 12""",
                (project_id, project["active_version_id"]),
            ).fetchall()
        ]
    return {
        "project": project,
        "version": version,
        "memories": memories,
        "memory_candidates": memory_candidates,
        "examples": examples,
    }


def should_propose_memory(message: str) -> bool:
    signals = (
        "我喜欢", "我不喜欢", "我叫", "我的生日", "我住", "我在",
        "以后记得", "你要记得", "我们约好", "下次", "一直",
    )
    return 6 <= len(message) <= 200 and any(signal in message for signal in signals)


def local_reply(message: str, project: dict[str, Any], examples: list[dict[str, Any]]) -> str:
    best = None
    best_score = 0
    chars = set(message)
    for example in examples:
        score = len(chars & set(example["context_text"]))
        if score > best_score:
            best = example
            best_score = score
    if best and best_score >= 2:
        return best["reply_text"]
    if any(word in message for word in ("慌", "害怕", "焦虑", "难受")):
        return "先别急，我在。要不要跟我说说最担心哪一块？"
    if any(word in message for word in ("开心", "成功", "通过", "搞定")):
        return "可以啊你，快讲讲怎么做到的。"
    if "？" in message or "?" in message:
        return "我觉得可以。你自己更偏向哪个？"
    return f"嗯，我在听。{project['display_name']}想陪你把这件事慢慢说清楚。"
