import json
import os
import shutil
import subprocess
from datetime import date
from typing import Any, Optional

from fastapi import HTTPException

from .services import import_messages


def binary_path() -> Optional[str]:
    configured = os.getenv("WX_CLI_BINARY")
    if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    return shutil.which("wx")


def run_wx(arguments: list[str], timeout: int = 30) -> str:
    executable = binary_path()
    if not executable:
        raise HTTPException(status_code=503, detail="未安装 wx-cli")
    safe_environment = {
        key: value
        for key in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
        if (value := os.environ.get(key))
    }
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            check=False,
            env=safe_environment,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="读取微信超时，请检查微信是否正在运行") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise HTTPException(
            status_code=503,
            detail=detail[:500] or "wx-cli 无法读取微信，请先在终端运行 wx init",
        )
    return result.stdout


def connector_status() -> dict[str, Any]:
    executable = binary_path()
    if not executable:
        return {
            "installed": False,
            "ready": False,
            "version": None,
            "setup_command": "npm install -g @jackwener/wx-cli",
        }
    try:
        version = run_wx(["--version"], timeout=5).strip()
        run_wx(["sessions", "--json", "--limit", "1"], timeout=10)
    except HTTPException as error:
        return {
            "installed": True,
            "ready": False,
            "version": version if "version" in locals() else None,
            "detail": error.detail,
            "setup_command": "wx init",
        }
    return {"installed": True, "ready": True, "version": version}


def _json_output(arguments: list[str], timeout: int = 30) -> Any:
    output = run_wx(arguments, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail="wx-cli 返回了无法识别的数据") from error


def _records(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def list_private_sessions(limit: int = 50) -> dict[str, Any]:
    payload = _json_output(["sessions", "--json", "--limit", str(limit)])
    sessions = []
    for item in _records(payload, ("sessions", "results", "data")):
        chat_type = str(item.get("chat_type") or "private")
        if chat_type != "private":
            continue
        display_name = (
            item.get("display")
            or item.get("name")
            or item.get("chat")
            or item.get("chat_name")
            or item.get("nickname")
        )
        if not display_name:
            continue
        sessions.append(
            {
                "name": str(display_name),
                "chat_type": chat_type,
                "last_message": str(
                    item.get("last_message")
                    or item.get("summary")
                    or item.get("content")
                    or ""
                )[:120],
                "last_timestamp": item.get("last_timestamp")
                or item.get("timestamp")
                or item.get("time"),
            }
        )
    return {"sessions": sessions, "meta": payload.get("meta", {}) if isinstance(payload, dict) else {}}


def fetch_private_history(
    chat: str,
    self_speaker: str,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    messages = fetch_private_history_page(
        chat=chat,
        self_speaker=self_speaker,
        since=since,
        until=until,
        limit=limit,
        offset=0,
    )
    if not messages:
        raise HTTPException(status_code=422, detail="没有读取到该联系人的文本消息")
    return [
        {
            "speaker": item["speaker"],
            "text": item["normalized_text"],
            "timestamp": item["timestamp"] or item["sent_at"],
        }
        for item in messages
    ]


def fetch_private_history_page(
    chat: str,
    self_speaker: str,
    since: Optional[date],
    until: Optional[date],
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    arguments = [
        "history",
        chat,
        "--json",
        "--type",
        "text",
        "--limit",
        str(limit),
        "--offset",
        str(offset),
    ]
    if since:
        arguments.extend(["--since", since.isoformat()])
    if until:
        arguments.extend(["--until", until.isoformat()])
    payload = _json_output(arguments, timeout=60)
    messages = []
    for item in _records(payload, ("messages", "results", "data")):
        content = item.get("content") or item.get("text")
        if not isinstance(content, str) or not content.strip():
            continue
        sender = item.get("sender")
        direction_keys = (
            "is_self",
            "from_self",
            "from_me",
            "is_send",
            "sender_is_self",
        )
        explicit_direction = [
            item[key] for key in direction_keys if key in item
        ]
        is_self = (
            any(bool(value) for value in explicit_direction)
            if explicit_direction
            else bool(str(sender or "").strip())
        )
        speaker = self_speaker if is_self else chat
        messages.append(
            {
                "speaker": speaker,
                "raw_text": content,
                "normalized_text": content.strip(),
                "sent_at": item.get("time")
                or item.get("create_time")
                or item.get("sent_at"),
                "timestamp": item.get("timestamp"),
                "source_message_id": str(item["local_id"])
                if item.get("local_id") is not None
                else None,
            }
        )
    return messages


def preview_history(messages: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [item["timestamp"] for item in messages if item.get("timestamp")]
    return {
        "message_count": len(messages),
        "participants": sorted({item["speaker"] for item in messages}),
        "start_time": min(timestamps) if timestamps else None,
        "end_time": max(timestamps) if timestamps else None,
        "sample": messages[-5:],
    }


def import_wechat_history(
    project_id: str,
    owner_user_id: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    content = "\n".join(json.dumps(item, ensure_ascii=False) for item in messages)
    return import_messages(project_id, owner_user_id, content, "jsonl")
