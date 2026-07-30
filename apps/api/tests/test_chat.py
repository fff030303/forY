from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_chat() -> None:
    response = client.post("/chat", json={"message": "你好"})

    assert response.status_code == 200
    assert response.json() == {"reply": "你好呀"}


def test_chat_rejects_missing_message() -> None:
    response = client.post("/chat", json={})

    assert response.status_code == 422


def test_chat_rejects_empty_message() -> None:
    response = client.post("/chat", json={"message": ""})

    assert response.status_code == 422
