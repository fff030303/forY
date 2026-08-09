import os
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.database import connect, init_database
from app.import_pipeline import (
    _merge_analyses,
    _refine_style_traits,
    create_import_job,
    get_import_job,
    prepare_import_job_reanalysis,
    prepare_import_job_resume,
    run_import_job,
)
from app.services import _has_unsupported_causal_claim, build_persona


def message(
    local_id: int,
    speaker: str,
    text: str,
    timestamp: int,
) -> dict:
    return {
        "source_message_id": str(local_id),
        "speaker": speaker,
        "raw_text": text,
        "normalized_text": text,
        "sent_at": "2026-07-30 12:00",
        "timestamp": timestamp,
    }


def configure_database(tmp_path: Path) -> None:
    os.environ["PERSONA_DB_PATH"] = str(tmp_path / "pipeline.db")
    init_database()
    with connect() as db:
        db.execute(
            """INSERT INTO projects (
            id, owner_user_id, display_name, relationship_type,
            consent_status, status, created_at, updated_at
            ) VALUES ('project-1', 'demo-user', '小林', '朋友',
            'confirmed', 'draft', 'now', 'now')"""
        )


def test_imports_pages_and_creates_chunks(tmp_path: Path) -> None:
    configure_database(tmp_path)
    job = create_import_job("project-1", "小林", "我", None, None, 2, False)
    pages = [
        [
            message(1, "我", "你好", 100),
            message(2, "小林", "在呢", 101),
        ],
        [message(3, "我", "明天见", 102)],
    ]

    with patch("app.import_pipeline.fetch_private_history_page", side_effect=pages):
        run_import_job(job["id"])

    result = get_import_job("project-1", job["id"])
    assert result["status"] == "completed"
    assert result["next_offset"] == 3
    assert result["imported_count"] == 3
    assert result["chunk_count"] == 1
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM source_messages").fetchone()[0] == 3


def test_resumes_from_saved_offset(tmp_path: Path) -> None:
    configure_database(tmp_path)
    job = create_import_job("project-1", "小林", "我", None, None, 2, False)
    first_page = [
        message(1, "我", "你好", 100),
        message(2, "小林", "在呢", 101),
    ]

    with patch(
        "app.import_pipeline.fetch_private_history_page",
        side_effect=[first_page, HTTPException(status_code=503, detail="暂时失败")],
    ):
        run_import_job(job["id"])

    failed = get_import_job("project-1", job["id"])
    assert failed["status"] == "failed"
    assert failed["next_offset"] == 2
    prepare_import_job_resume("project-1", job["id"])

    with patch(
        "app.import_pipeline.fetch_private_history_page",
        return_value=[message(3, "我", "继续", 102)],
    ) as fetch:
        run_import_job(job["id"])

    assert fetch.call_args.kwargs["offset"] == 2
    resumed = get_import_job("project-1", job["id"])
    assert resumed["status"] == "completed"
    assert resumed["imported_count"] == 3


def test_completed_import_can_be_reset_for_new_emotion_analysis(
    tmp_path: Path,
) -> None:
    configure_database(tmp_path)
    job = create_import_job("project-1", "小林", "我", None, None, 100, False)
    with patch(
        "app.import_pipeline.fetch_private_history_page",
        return_value=[
            message(1, "我", "今天真倒霉", 100),
            message(2, "小林", "怎么了", 101),
        ],
    ):
        run_import_job(job["id"])

    reset = prepare_import_job_reanalysis("project-1", job["id"])

    assert reset["status"] == "queued"
    assert reset["analyzed_chunk_count"] == 0
    with connect() as db:
        chunk = db.execute(
            "SELECT status, analysis_json FROM analysis_chunks WHERE import_job_id = ?",
            (job["id"],),
        ).fetchone()
    assert chunk["status"] == "pending"
    assert chunk["analysis_json"] is None


def test_analysis_keeps_source_message_ids(tmp_path: Path) -> None:
    configure_database(tmp_path)
    job = create_import_job("project-1", "小林", "我", None, None, 100, True)
    page = [
        message(1, "我", "你还好吗", 100),
        message(2, "小林", "我在，别担心", 101),
        message(3, "小林", "慢慢说就好", 102),
    ]
    analysis = {
        "topics": [
            {"title": "情绪安慰", "start_index": 1, "end_index": 2, "summary": ""}
        ],
        "style_traits": [
            {
                "name": "安慰方式",
                "value": "简短直接",
                "confidence": 0.8,
                "evidence_indexes": [2],
            }
        ],
        "memory_candidates": [
            {
                "content": "对方会用简短的话安慰用户",
                "importance": 0.7,
                "evidence_indexes": [2],
            }
        ],
        "reply_examples": [
            {"context_index": 1, "reply_index": 2, "quality_reason": "自然"}
        ],
    }
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cache_hit_tokens": 20,
        "cache_miss_tokens": 80,
    }

    with patch(
        "app.import_pipeline.fetch_private_history_page", return_value=page
    ), patch("app.import_pipeline.deepseek_json", return_value=(analysis, usage)):
        run_import_job(job["id"])

    result = get_import_job("project-1", job["id"])
    assert result["status"] == "completed"
    assert result["analyzed_chunk_count"] == 1
    trait = result["summary"]["style_traits"][0]
    assert len(trait["evidence_message_ids"]) == 1
    example = result["summary"]["reply_examples"][0]
    assert example["context_message_id"]
    assert example["reply_message_id"]
    with connect() as db:
        db.execute(
            """UPDATE projects SET target_speaker = '小林', user_speaker = '我'
            WHERE id = 'project-1'"""
        )
    persona = build_persona("project-1", "demo-user")
    assert persona["version"]["traits"][0]["name"] == "安慰方式"
    assert persona["version"]["traits"][0]["source_message_ids"]
    assert persona["memories"][0]["source_message_ids"]
    assert persona["examples"][0]["source_message_ids"]


def test_final_merge_compacts_duplicate_results_before_one_ai_call() -> None:
    analyses = [
        {
            "style_traits": [{
                "name": "耐心",
                "value": "会持续回应",
                "confidence": 0.5 + index / 100,
                "evidence_message_ids": [f"message-{index}"],
            }],
            "memory_candidates": [{
                "content": f"共同事件 {index}",
                "importance": index / 100,
                "evidence_message_ids": [f"message-{index}"],
            }],
        }
        for index in range(40)
    ]
    merged = {
        "summary": "长期交流中表现耐心",
        "style_traits": analyses[0]["style_traits"],
    }
    with patch(
        "app.import_pipeline.deepseek_json",
        return_value=(merged, {}),
    ) as deepseek:
        result = _merge_analyses(analyses)

    assert deepseek.call_count == 1
    sent = deepseek.call_args.args[1]
    assert len(sent) < 30000
    assert result["summary"] == "长期交流中表现耐心"
    assert len(result["memory_candidates"]) == 30


def test_final_merge_uses_local_compaction_when_json_is_truncated() -> None:
    analyses = [{
        "memory_candidates": [{
            "content": "一起旅行过",
            "importance": 0.9,
            "evidence_message_ids": ["message-1"],
        }],
    }]
    with patch(
        "app.import_pipeline.deepseek_json",
        side_effect=HTTPException(
            status_code=502,
            detail="DeepSeek 连续两次返回了不完整的 JSON，请稍后续传",
        ),
    ):
        result = _merge_analyses([*analyses, *analyses])

    assert result["memory_candidates"][0]["content"] == "一起旅行过"


def test_final_merge_requires_repeated_evidence_for_stable_traits() -> None:
    analyses = [
        {
            "style_traits": [{
                "name": "使用表情符号",
                "value": "晚安时使用表情",
                "confidence": 1.0,
                "evidence_message_ids": ["message-1"],
            }],
        },
        {
            "style_traits": [{
                "name": "直率表达",
                "value": "直接说明情绪",
                "confidence": 0.8,
                "evidence_message_ids": ["message-2"],
            }],
        },
        {
            "style_traits": [{
                "name": "直率表达",
                "value": "不绕弯子",
                "confidence": 0.9,
                "evidence_message_ids": ["message-3"],
            }],
        },
    ]
    with patch(
        "app.import_pipeline.deepseek_json",
        side_effect=HTTPException(status_code=502, detail="JSON 不完整"),
    ):
        result = _merge_analyses(analyses)

    assert [item["name"] for item in result["style_traits"]] == ["直率表达"]
    assert result["style_traits"][0]["evidence_message_ids"] == [
        "message-2", "message-3"
    ]


def test_style_refinement_rejects_hallucinated_evidence_ids() -> None:
    traits = [
        {
            "name": "情绪直白",
            "value": "直接表达情绪",
            "confidence": 0.9,
            "evidence_message_ids": ["message-1", "message-2"],
        },
        {
            "name": "情绪化表达",
            "value": "使用感叹词",
            "confidence": 0.8,
            "evidence_message_ids": ["message-3", "message-4"],
        },
    ]
    payload = {
        "style_traits": [{
            "name": "情绪表达直接",
            "value": "会直接表达情绪并使用感叹词",
            "confidence": 0.85,
            "evidence_message_ids": [
                "message-1", "message-3", "hallucinated-message"
            ],
        }],
    }
    with patch(
        "app.import_pipeline.deepseek_json",
        return_value=(payload, {}),
    ):
        result = _refine_style_traits(traits)

    assert result[0]["evidence_message_ids"] == ["message-1", "message-3"]


def test_rejects_causal_claim_when_evidence_only_shows_cooccurrence() -> None:
    texts = {
        "message-1": "磕着头了",
        "message-2": "电脑里面的数据没了",
    }
    assert _has_unsupported_causal_claim(
        "磕头导致电脑数据丢失",
        ["message-1", "message-2"],
        texts,
    )
    assert not _has_unsupported_causal_claim(
        "因为电脑进水，所以数据丢失",
        ["message-3"],
        {"message-3": "因为电脑进水，所以数据丢失了"},
    )


def test_stable_emotion_requires_evidence_from_independent_dates() -> None:
    analyses = [
        {
            "emotional_patterns": [{
                "emotion": "烦躁",
                "triggers": "工作受阻",
                "confidence": 0.9,
                "evidence_message_ids": ["message-1", "message-2"],
                "scene_dates": ["2026-07-01"],
                "scene_count": 1,
            }]
        },
        {
            "emotional_patterns": [{
                "emotion": "无奈",
                "triggers": "接连遇到不顺",
                "confidence": 0.8,
                "evidence_message_ids": ["message-3", "message-4"],
                "scene_dates": ["2026-07-01", "2026-07-12"],
                "scene_count": 2,
            }]
        },
    ]
    with patch(
        "app.import_pipeline.deepseek_json",
        side_effect=HTTPException(status_code=502, detail="JSON 不完整"),
    ):
        result = _merge_analyses(analyses)

    assert [item["emotion"] for item in result["emotional_patterns"]] == ["无奈"]
