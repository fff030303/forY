import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import init_database
from app.main import app
from app.wechat_connector import (
    fetch_private_history,
    list_private_sessions,
    preview_history,
)


def test_lists_only_private_sessions() -> None:
    payload = {
        "sessions": [
            {
                "chat": "小林",
                "chat_type": "private",
                "summary": "明天见",
                "timestamp": 123,
            },
            {"display": "工作群", "chat_type": "group"},
        ],
        "meta": {"status": "ok"},
    }
    with patch("app.wechat_connector.run_wx", return_value=json.dumps(payload)):
        result = list_private_sessions()

    assert result["sessions"] == [
        {
            "name": "小林",
            "chat_type": "private",
            "last_message": "明天见",
            "last_timestamp": 123,
        }
    ]
    assert result["meta"]["status"] == "ok"


def test_normalizes_private_history_without_learning_system_messages() -> None:
    payload = {
        "messages": [
            {"sender": "我的昵称", "content": "明天见", "timestamp": "2025-02-01"},
            {"sender": "", "content": "好", "timestamp": "2025-02-02"},
            {"sender": "", "content": "", "timestamp": "2025-02-03"},
        ]
    }
    with patch("app.wechat_connector.run_wx", return_value=json.dumps(payload)):
        messages = fetch_private_history(
            "小林", "我", date(2025, 1, 1), date(2025, 12, 31), 500
        )

    assert messages == [
        {"speaker": "我", "text": "明天见", "timestamp": "2025-02-01"},
        {"speaker": "小林", "text": "好", "timestamp": "2025-02-02"},
    ]
    assert preview_history(messages)["message_count"] == 2


def test_rejects_reversed_date_range() -> None:
    with pytest.raises(HTTPException) as error:
        fetch_private_history(
            "小林", "我", date(2025, 12, 31), date(2025, 1, 1), 500
        )
    assert error.value.status_code == 422


def test_wechat_preview_and_import_endpoints(tmp_path: Path) -> None:
    os.environ["PERSONA_DB_PATH"] = str(tmp_path / "wechat.db")
    init_database()
    messages = [
        {"speaker": "我", "text": "明天见", "timestamp": "2025-02-01"},
        {"speaker": "小林", "text": "记得带伞", "timestamp": "2025-02-02"},
    ]

    with TestClient(app) as client, patch(
        "app.main.fetch_private_history", return_value=messages
    ):
        project = client.post(
            "/projects",
            json={
                "display_name": "小林",
                "relationship_type": "朋友",
                "consent_confirmed": True,
            },
        ).json()
        request = {
            "chat": "小林",
            "self_speaker": "我",
            "since": "2025-01-01",
            "until": "2025-12-31",
            "limit": 500,
        }

        preview = client.post("/wechat/preview", json=request)
        assert preview.status_code == 200
        assert preview.json()["message_count"] == 2

        imported = client.post(
            f"/projects/{project['id']}/wechat/import", json=request
        )
        assert imported.status_code == 200
        assert imported.json()["inserted_count"] == 2
        assert imported.json()["participants"] == [
            {"name": "我", "message_count": 1},
            {"name": "小林", "message_count": 1},
        ]


def test_full_import_job_endpoint(tmp_path: Path) -> None:
    os.environ["PERSONA_DB_PATH"] = str(tmp_path / "full-import.db")
    init_database()
    page = [
        {
            "source_message_id": "1",
            "speaker": "我",
            "raw_text": "你好",
            "normalized_text": "你好",
            "sent_at": "2026-07-30 12:00",
            "timestamp": 100,
        },
        {
            "source_message_id": "2",
            "speaker": "小林",
            "raw_text": "在呢",
            "normalized_text": "在呢",
            "sent_at": "2026-07-30 12:01",
            "timestamp": 101,
        },
    ]

    with TestClient(app) as client, patch(
        "app.import_pipeline.fetch_private_history_page", return_value=page
    ):
        project = client.post(
            "/projects",
            json={
                "display_name": "小林",
                "relationship_type": "朋友",
                "consent_confirmed": True,
            },
        ).json()
        created = client.post(
            f"/projects/{project['id']}/wechat/import-jobs",
            json={
                "chat": "小林",
                "self_speaker": "我",
                "page_size": 100,
                "analyze": False,
            },
        )
        assert created.status_code == 202
        job_id = created.json()["id"]
        status = client.get(
            f"/projects/{project['id']}/wechat/import-jobs/{job_id}"
        )

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["imported_count"] == 2
    assert sorted(status.json()["participants"], key=lambda item: item["name"]) == [
        {"name": "小林", "message_count": 1},
        {"name": "我", "message_count": 1},
    ]
