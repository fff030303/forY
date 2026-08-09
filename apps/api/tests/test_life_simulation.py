from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import connect, init_database
from app.life_simulation import (
    generate_daily_plan,
    get_life_snapshot,
    save_life_guidance,
)


def _project(project_id: str) -> None:
    init_database()
    with connect() as db:
        db.execute(
            """INSERT INTO projects (
            id, owner_user_id, display_name, relationship_type,
            consent_status, status, created_at, updated_at
            ) VALUES (?, 'demo-user', '小林', '朋友',
            'confirmed', 'ready', 'now', 'now')""",
            (project_id,),
        )


def _message(
    project_id: str, message_id: str, sent_at: str, text: str
) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO source_messages (
            id, project_id, fingerprint, speaker, sent_at,
            normalized_text, source_line, created_at
            ) VALUES (?, ?, ?, '小林', ?, ?, 1, 'now')""",
            (message_id, project_id, message_id, sent_at, text),
        )


def test_life_advances_without_model_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-1")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' WHERE id = 'project-1'"
        )
    _message("project-1", "m1", "2026-07-28 12:10", "去吃午饭了")
    _message("project-1", "m2", "2026-07-29 12:20", "来吃饭了")
    _message("project-1", "m3", "2026-07-30 12:15", "今天有我的午饭吗")

    noon = datetime(2026, 7, 31, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    snapshot = get_life_snapshot("project-1", "Asia/Shanghai", noon)

    assert snapshot["is_simulated"] is True
    assert "可能在吃午饭" in snapshot["state"]["activity"]
    assert snapshot["state"]["confidence"] >= 0.6
    assert snapshot["state"]["energy"] is None
    assert any(event["event_type"] == "lunch" for event in snapshot["events"])
    assert snapshot["events"][0]["evidence_message_ids"]


def test_life_events_are_not_duplicated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-2")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' WHERE id = 'project-2'"
        )
    _message("project-2", "m1", "2026-07-28 12:10", "去吃午饭了")
    _message("project-2", "m2", "2026-07-29 12:20", "来吃饭了")
    _message("project-2", "m3", "2026-07-30 12:15", "今天有我的午饭吗")
    evening = datetime(2026, 7, 31, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = get_life_snapshot("project-2", "Asia/Shanghai", evening)
    second = get_life_snapshot("project-2", "Asia/Shanghai", evening)

    assert len(second["events"]) == len(first["events"])


def test_late_night_state_is_sleepy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-3")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' WHERE id = 'project-3'"
        )
    _message("project-3", "m1", "2026-07-28 23:50", "准备睡觉")
    _message("project-3", "m2", "2026-07-29 23:58", "我要睡觉")
    _message("project-3", "m3", "2026-07-30 23:45", "好困，准备睡觉")
    _message("project-3", "m4", "2026-07-31 23:40", "好困")
    late = datetime(2026, 7, 31, 23, 55, tzinfo=ZoneInfo("Asia/Shanghai"))

    snapshot = get_life_snapshot("project-3", "Asia/Shanghai", late)

    assert snapshot["state"]["sleepiness"] >= 80
    assert snapshot["state"]["mood"] == "最近明确表达过困倦"


def test_unknown_when_chat_has_no_routine_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-4")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' WHERE id = 'project-4'"
        )
    _message("project-4", "m1", "2026-07-30 12:00", "今天天气不错")

    snapshot = get_life_snapshot(
        "project-4",
        "Asia/Shanghai",
        datetime(2026, 7, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["state"]["activity_code"] == "unknown"
    assert snapshot["events"] == []
    assert snapshot["state"]["energy"] is None


def test_wake_routine_uses_first_workday_activity_not_after_midnight(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-wake")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' "
            "WHERE id = 'project-wake'"
        )
    workdays = [
        ("2026-07-20", "09:05"), ("2026-07-21", "09:20"),
        ("2026-07-22", "09:15"), ("2026-07-23", "09:30"),
        ("2026-07-24", "09:10"),
    ]
    for index, (day, first_time) in enumerate(workdays):
        _message("project-wake", f"night-{index}", f"{day} 01:20", "还没睡")
        _message(
            "project-wake", f"morning-{index}",
            f"{day} {first_time}", "早啊",
        )
    for index, day in enumerate(("2026-07-25", "2026-07-26", "2026-08-01")):
        _message(
            "project-wake", f"weekend-{index}", f"{day} 12:30", "刚睡醒"
        )

    snapshot = get_life_snapshot(
        "project-wake", "Asia/Shanghai",
        datetime(2026, 7, 27, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    routines = {item["kind"]: item for item in snapshot["routine_profile"]}

    assert routines["wake"]["typical_time"] == "09:15"
    assert routines["wake"]["scope"] == "工作日"
    assert routines["wake"]["evidence_count"] == 5
    assert routines["weekend_wake"]["scope"] == "仅周末或休息日"
    assert "01:20" not in routines["wake"]["typical_time"]


def test_afternoon_wake_after_activity_is_only_a_nap_clue(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-nap-wake")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' "
            "WHERE id = 'project-nap-wake'"
        )
    for index, day in enumerate(("2026-07-20", "2026-07-21", "2026-07-22")):
        _message(
            "project-nap-wake", f"active-{index}", f"{day} 09:10", "上班了"
        )
        _message(
            "project-nap-wake", f"awake-{index}", f"{day} 14:00", "刚睡醒"
        )

    snapshot = get_life_snapshot(
        "project-nap-wake", "Asia/Shanghai",
        datetime(2026, 7, 23, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    routines = {item["kind"]: item for item in snapshot["routine_profile"]}

    assert routines["nap_wake"]["label"] == "午睡后再次醒来"
    assert routines["nap_wake"]["usable"] is True
    assert routines["nap_wake"]["predictable"] is False
    assert snapshot["state"]["activity_code"] == "unknown"


def test_user_guidance_generates_and_caches_creative_day(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PERSONA_DB_PATH", str(tmp_path / "life.db"))
    _project("project-5")
    with connect() as db:
        db.execute(
            "UPDATE projects SET target_speaker = '小林' WHERE id = 'project-5'"
        )
    guidance = (
        "她周一三五去公司，周二四居家上班。"
        "居家时可以摸鱼、陪小猫玩，偶尔和朋友出去。"
    )
    save_life_guidance("project-5", guidance)
    captured = {}

    def fake_json(system_prompt, user_prompt, max_tokens):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "summary": "居家办公但比较松弛的一天",
            "events": [
                {
                    "start_time": "09:00", "end_time": "10:00",
                    "activity": "赖床后起床", "location": "家里",
                    "mood": "懒洋洋", "condition": "正常",
                    "energy": 55, "hunger": 60,
                    "sleepiness": 45, "stress": 10,
                },
                {
                    "start_time": "10:00", "end_time": "12:00",
                    "activity": "居家工作，中间陪小猫玩", "location": "家里",
                    "mood": "轻松", "condition": "正常",
                    "energy": 65, "hunger": 35,
                    "sleepiness": 20, "stress": 25,
                },
                {
                    "start_time": "12:00", "end_time": "13:00",
                    "activity": "吃午饭", "location": "家里",
                    "mood": "开心", "condition": "正常",
                    "energy": 60, "hunger": 10,
                    "sleepiness": 30, "stress": 10,
                },
            ],
        }, {}

    monkeypatch.setattr("app.life_simulation.deepseek_json", fake_json)
    current = datetime(
        2026, 7, 30, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    generate_daily_plan("project-5", "Asia/Shanghai", current)
    snapshot = get_life_snapshot(
        "project-5", "Asia/Shanghai", current
    )

    assert guidance in captured["user"]
    assert "陪家里的猫" in captured["system"]
    assert snapshot["daily_plan"]["summary"] == "居家办公但比较松弛的一天"
    assert snapshot["state"]["activity"] == "居家工作，中间陪小猫玩"
    assert snapshot["state"]["energy"] == 65
    assert all(event["source"] == "user_guided_ai" for event in snapshot["events"])
