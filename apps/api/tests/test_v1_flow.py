import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.database import init_database
from app.main import app


SAMPLE_CHAT = """[2025-02-01 09:00] 我: 明天要面试了，有点慌
[2025-02-01 09:01] 小林: 别急，你准备得挺充分的
[2025-02-01 09:02] 我: 你还记得上次我们一起练习吗
[2025-02-01 09:03] 小林: 记得啊，你后来不是发挥得很好吗？
[2025-02-02 12:00] 我: 中午吃什么
[2025-02-02 12:01] 小林: 还是你喜欢的那家面馆？
"""


def test_complete_v1_flow(tmp_path: Path) -> None:
    os.environ["PERSONA_DB_PATH"] = str(tmp_path / "test.db")
    init_database()

    with TestClient(app) as client:
        created = client.post(
            "/projects",
            json={
                "display_name": "小林",
                "relationship_type": "老朋友",
                "consent_confirmed": True,
            },
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        imported = client.post(
            f"/projects/{project_id}/imports",
            json={"format": "wechat_text", "content": SAMPLE_CHAT},
        )
        assert imported.status_code == 200
        assert imported.json()["inserted_count"] == 6
        assert imported.json()["can_build"] is True

        identity = client.put(
            f"/projects/{project_id}/identity",
            json={"target_speaker": "小林", "user_speaker": "我"},
        )
        assert identity.status_code == 200

        persona = client.post(f"/projects/{project_id}/persona/build")
        assert persona.status_code == 200
        assert persona.json()["version"]["version_number"] == 1
        assert len(persona.json()["version"]["traits"]) == 3
        assert persona.json()["examples"]
        assert persona.json()["memories"]
        memory = persona.json()["memories"][0]
        trait = persona.json()["version"]["traits"][0]

        evidence = client.post(
            f"/projects/{project_id}/evidence",
            json={"message_ids": memory["source_message_ids"]},
        )
        assert evidence.status_code == 200
        assert evidence.json()["messages"]
        assert {"speaker", "sent_at", "text"} <= evidence.json()["messages"][0].keys()

        corrected_trait = client.put(
            f"/projects/{project_id}/persona/traits/0",
            json={
                "name": trait["name"],
                "value": "这是经过人工修正的人格结论",
                "confidence": 1,
            },
        )
        assert corrected_trait.status_code == 200
        assert corrected_trait.json()["version"]["traits"][0]["human_corrected"] is True
        assert corrected_trait.json()["version"]["traits"][0]["value"] == "这是经过人工修正的人格结论"

        edited_memory = client.put(
            f"/projects/{project_id}/memories/{memory['id']}",
            json={
                "content": "我们一起练习过面试",
                "importance": 0.9,
                "event_date": "2025-02-01",
            },
        )
        assert edited_memory.status_code == 200
        assert edited_memory.json()["content"] == "我们一起练习过面试"
        assert edited_memory.json()["event_date"] == "2025-02-01"

        chat = client.post(
            "/chat",
            json={"project_id": project_id, "message": "明天面试还是有点慌"},
        )
        assert chat.status_code == 200
        assert chat.json()["conversation_id"]
        assert chat.json()["message_id"]

        with patch(
            "app.main.deepseek_reply",
            return_value={"reply": "记住了。", "tone": "随意", "expression": "点头"},
        ) as generator:
            memory_chat = client.post(
                "/chat",
                json={
                    "project_id": project_id,
                    "conversation_id": chat.json()["conversation_id"],
                    "message": "我喜欢喝不加糖的冰美式",
                },
            )
        assert memory_chat.status_code == 200
        history = generator.call_args.args[6]
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[0]["content"] == "明天面试还是有点慌"
        restored = client.get(
            f"/projects/{project_id}/conversations/{chat.json()['conversation_id']}"
        )
        assert restored.status_code == 200
        assert [item["content"] for item in restored.json()["messages"]][-2:] == [
            "我喜欢喝不加糖的冰美式", "记住了。",
        ]
        latest = client.get(f"/projects/{project_id}/latest-conversation")
        assert latest.status_code == 200
        assert latest.json()["conversation_id"] == chat.json()["conversation_id"]
        candidate_id = memory_chat.json()["memory_candidate_id"]
        assert candidate_id

        pending_persona = client.get(f"/projects/{project_id}/persona")
        assert pending_persona.json()["memory_candidates"][0]["id"] == candidate_id

        approved = client.post(
            f"/projects/{project_id}/memories/{candidate_id}/approve"
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "active"

        feedback = client.post(
            f"/projects/{project_id}/messages/{chat.json()['message_id']}/feedback",
            json={
                "rating": "dislike",
                "reason": "不像他平时的语气",
                "ideal_reply": "别慌，你已经准备好了",
            },
        )
        assert feedback.status_code == 201
        assert feedback.json()["status"] == "candidate"

        published = client.post(
            f"/projects/{project_id}/candidates/publish",
            json={"feedback_ids": [feedback.json()["id"]]},
        )
        assert published.status_code == 200
        assert published.json()["version"]["version_number"] == 2
        assert len(published.json()["examples"]) == 4

        versions = client.get(f"/projects/{project_id}/versions")
        assert [item["version_number"] for item in versions.json()] == [2, 1]

        rolled_back = client.post(
            f"/projects/{project_id}/versions/{versions.json()[1]['id']}/activate"
        )
        assert rolled_back.status_code == 200
        assert rolled_back.json()["version"]["version_number"] == 1


def test_consent_ownership_and_project_deletion(tmp_path: Path) -> None:
    os.environ["PERSONA_DB_PATH"] = str(tmp_path / "privacy.db")
    init_database()

    with TestClient(app) as client:
        rejected = client.post(
            "/projects",
            json={
                "display_name": "测试",
                "relationship_type": "朋友",
                "consent_confirmed": False,
            },
        )
        assert rejected.status_code == 422

        created = client.post(
            "/projects",
            headers={"X-User-Id": "owner-a"},
            json={
                "display_name": "测试",
                "relationship_type": "朋友",
                "consent_confirmed": True,
            },
        )
        project_id = created.json()["id"]

        hidden = client.get("/projects", headers={"X-User-Id": "owner-b"})
        assert hidden.json() == []
        forbidden = client.delete(
            f"/projects/{project_id}", headers={"X-User-Id": "owner-b"}
        )
        assert forbidden.status_code == 404

        deleted = client.delete(
            f"/projects/{project_id}", headers={"X-User-Id": "owner-a"}
        )
        assert deleted.status_code == 204
