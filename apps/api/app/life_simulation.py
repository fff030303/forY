import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from .database import connect, row_to_dict
from .deepseek_service import deepseek_json
from .services import new_id, now


ROUTINE_RULES = {
    "sleep": {
        "label": "准备睡觉",
        "patterns": (
            r"准备睡觉", r"我要睡觉", r"我去睡", r"我很想睡",
            r"睡觉睡觉", r"哄自己睡觉", r"晚安",
        ),
        "hours": set(range(21, 24)) | set(range(0, 4)),
    },
    "lunch": {
        "label": "吃午饭",
        "patterns": (
            r"午饭", r"午餐", r"去吃饭", r"来吃饭", r"在吃饭",
            r"吃饭了",
        ),
        "hours": set(range(10, 15)),
    },
    "nap": {
        "label": "午休或午睡",
        "patterns": (r"午休", r"午睡", r"中午.{0,8}睡"),
        "hours": set(range(11, 16)),
    },
    "dinner": {
        "label": "吃晚饭",
        "patterns": (
            r"晚饭", r"晚餐", r"去吃饭", r"来吃饭", r"在吃饭",
            r"吃饭了",
        ),
        "hours": set(range(17, 23)),
    },
}

SELF_WAKE_PATTERN = re.compile(
    r"^(?:我)?(?:刚|才)?(?:睡)?醒(?:了)?|^(?:我)?起床了"
)


def _timezone(name: Optional[str]) -> tuple[ZoneInfo, str]:
    try:
        return ZoneInfo(name) if name else ZoneInfo("Asia/Shanghai"), (
            name or "Asia/Shanghai"
        )
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai"), "Asia/Shanghai"


def _parse_sent_at(value: str, timezone: ZoneInfo) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed


def _circular_minute(kind: str, value: int) -> int:
    if kind == "sleep" and value < 4 * 60:
        return value + 24 * 60
    return value


def _clock_text(value: int) -> str:
    value %= 24 * 60
    return f"{value // 60:02d}:{value % 60:02d}"


def _extract_routines(
    messages: list[dict[str, Any]], timezone: ZoneInfo
) -> list[dict[str, Any]]:
    parsed_messages: list[tuple[datetime, str, str]] = []
    seen_messages: set[tuple[str, str]] = set()
    for message in messages:
        sent_at = _parse_sent_at(message["sent_at"], timezone)
        if not sent_at:
            continue
        sent_at = sent_at.astimezone(timezone)
        text = message["normalized_text"].strip()
        duplicate_key = (sent_at.isoformat(), text)
        if duplicate_key in seen_messages:
            continue
        seen_messages.add(duplicate_key)
        parsed_messages.append((sent_at, message["id"], text))
    parsed_messages.sort(key=lambda item: item[0])

    matches: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    by_day: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for sent_at, message_id, text in parsed_messages:
        by_day[sent_at.date().isoformat()].append((sent_at, message_id, text))
        for kind, rule in ROUTINE_RULES.items():
            if sent_at.hour not in rule["hours"]:
                continue
            if any(re.search(pattern, text) for pattern in rule["patterns"]):
                matches[kind].append((sent_at, message_id))

    # A message only proves the person was awake by then. Use the first daytime
    # message on workdays as a weak upper bound, never a 00:00-04:59 message.
    for day_messages in by_day.values():
        morning = [
            item for item in day_messages
            if 5 * 60 <= item[0].hour * 60 + item[0].minute < 12 * 60
        ]
        if morning and morning[0][0].weekday() < 5:
            matches["wake"].append((morning[0][0], morning[0][1]))

        for index, (sent_at, message_id, text) in enumerate(day_messages):
            minute = sent_at.hour * 60 + sent_at.minute
            if not SELF_WAKE_PATTERN.search(text):
                continue
            earlier_daytime = any(
                5 * 60 <= previous[0].hour * 60 + previous[0].minute
                and previous[0] <= sent_at - timedelta(minutes=30)
                for previous in day_messages[:index]
            )
            if 11 * 60 + 30 <= minute < 19 * 60 and earlier_daytime:
                matches["nap_wake"].append((sent_at, message_id))
            elif (
                11 * 60 + 30 <= minute < 15 * 60 + 30
                and sent_at.weekday() >= 5
                and not earlier_daytime
            ):
                matches["weekend_wake"].append((sent_at, message_id))

    routines = []
    for kind, items in matches.items():
        days = sorted({sent_at.date().isoformat() for sent_at, _ in items})
        minutes = [
            _circular_minute(kind, sent_at.hour * 60 + sent_at.minute)
            for sent_at, _ in items
        ]
        typical = round(median(minutes))
        independent_days = len(days)
        minimum_days = 5 if kind == "wake" else 3
        confidence = min(0.95, 0.35 + independent_days * 0.1)
        if kind == "wake":
            confidence = min(0.8, 0.25 + independent_days * 0.05)
        labels = {
            "wake": "当天首次活跃（推测已醒）",
            "weekend_wake": "周末或休息日晚起",
            "nap_wake": "午睡后再次醒来",
        }
        scopes = {
            "wake": ("weekday", "工作日"),
            "weekend_wake": ("weekend", "仅周末或休息日"),
            "nap_wake": ("conditional", "仅当天此前已活跃时"),
        }
        scope_code, scope = scopes.get(kind, ("all", "不限日期"))
        predictable = kind != "nap_wake" and independent_days >= minimum_days
        if kind == "wake":
            basis = (
                f"取 {independent_days} 个工作日 05:00 后、12:00 前的第一条消息；"
                "它表示当时已经醒着，不等于精确起床时间。"
            )
        elif kind == "weekend_wake":
            basis = (
                f"有 {len(items)} 次周末中午首次出现“刚醒/睡醒”类表达，"
                f"分布在 {independent_days} 个日期；只作为周末或休息日模式。"
            )
        elif kind == "nap_wake":
            basis = (
                f"有 {len(items)} 次午后醒来前当天已经出现过消息；"
                "更像午睡结束，只作为条件事件，不作为每日起床时间。"
            )
        else:
            basis = (
                f"聊天记录中有 {len(items)} 条直接相关表达，"
                f"分布在 {independent_days} 个不同日期。"
            )
        routines.append({
            "kind": kind,
            "label": labels[kind] if kind in labels else ROUTINE_RULES[kind]["label"],
            "typical_time": _clock_text(typical),
            "typical_minute": typical % (24 * 60),
            "evidence_count": len(items),
            "independent_days": independent_days,
            "confidence": confidence,
            "usable": independent_days >= minimum_days,
            "predictable": predictable,
            "scope": scope,
            "scope_code": scope_code,
            "evidence_message_ids": list(dict.fromkeys(
                message_id for _, message_id in items
            ))[-12:],
            "basis": basis,
        })

    lunch = next((item for item in routines if item["kind"] == "lunch"), None)
    weekday_wake = next(
        (item for item in routines if item["kind"] == "wake"), None
    )
    if (
        lunch and weekday_wake and lunch["usable"] and weekday_wake["usable"]
        and weekday_wake["typical_minute"] >= lunch["typical_minute"]
    ):
        weekday_wake["predictable"] = False
        weekday_wake["basis"] += " 与午饭时间顺序冲突，因此不用于日程推演。"
    return sorted(routines, key=lambda item: item["typical_minute"])


def _recent_state(
    messages: list[dict[str, Any]], local: datetime, timezone: ZoneInfo
) -> dict[str, Any]:
    result = {
        "mood": "未知",
        "condition": "未知",
        "energy": None,
        "hunger": None,
        "sleepiness": None,
        "health": None,
        "stress": None,
        "evidence_message_ids": [],
    }
    for message in reversed(messages):
        sent_at = _parse_sent_at(message["sent_at"], timezone)
        if not sent_at or sent_at > local:
            continue
        age = local - sent_at
        text = message["normalized_text"]
        if age <= timedelta(hours=6) and re.search(r"困|想睡", text):
            result["sleepiness"] = 82
            result["energy"] = 28
            result["mood"] = "最近明确表达过困倦"
            result["evidence_message_ids"].append(message["id"])
        if age <= timedelta(hours=3) and re.search(r"饿|没吃饭", text):
            result["hunger"] = 78
            result["evidence_message_ids"].append(message["id"])
        if age <= timedelta(hours=36) and re.search(
            r"我.{0,5}(感冒|发烧|头疼|头痛|不舒服|嗓子疼)", text
        ):
            result["condition"] = "最近明确表达过身体不舒服"
            result["health"] = 72
            result["evidence_message_ids"].append(message["id"])
        if age <= timedelta(hours=8) and re.search(r"烦|累死|压力", text):
            result["stress"] = 72
            result["mood"] = "最近明确表达过疲惫或烦躁"
            result["evidence_message_ids"].append(message["id"])
    result["evidence_message_ids"] = list(dict.fromkeys(
        result["evidence_message_ids"]
    ))
    return result


def _routine_applies(
    routine: dict[str, Any], routines: list[dict[str, Any]], weekday: int
) -> bool:
    scope = routine.get("scope_code", "all")
    if (
        not routine["usable"] or not routine.get("predictable", True)
        or (scope == "weekday" and weekday >= 5)
        or (scope == "weekend" and weekday < 5)
    ):
        return False
    if weekday >= 5 and routine["kind"] == "lunch":
        weekend_wake = next((
            item for item in routines
            if item["kind"] == "weekend_wake"
            and item["usable"] and item.get("predictable", True)
        ), None)
        if (
            weekend_wake
            and routine["typical_minute"] <= weekend_wake["typical_minute"]
        ):
            return False
    return True


def _projected_activity(
    routines: list[dict[str, Any]], minute: int, weekday: int
) -> Optional[dict[str, Any]]:
    candidates = []
    for routine in routines:
        if not _routine_applies(routine, routines, weekday):
            continue
        distance = abs(minute - routine["typical_minute"])
        if routine["kind"] == "sleep":
            distance = min(distance, 24 * 60 - distance)
        if distance <= 50:
            candidates.append((distance, routine))
    return min(candidates, default=(0, None), key=lambda item: item[0])[1]


def save_life_guidance(project_id: str, guidance: str) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO life_settings (project_id, guidance, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
            guidance=excluded.guidance, updated_at=excluded.updated_at""",
            (project_id, guidance.strip(), now()),
        )


def _valid_time(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23 or minute > 59:
        return None
    return value


def _number(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return max(0, min(100, round(value)))
    return None


def generate_daily_plan(
    project_id: str,
    timezone_name: Optional[str] = None,
    at: Optional[datetime] = None,
) -> dict[str, Any]:
    timezone, safe_name = _timezone(timezone_name)
    local = (at or datetime.now(timezone)).astimezone(timezone)
    with connect() as db:
        project = row_to_dict(
            db.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        )
        setting = db.execute(
            "SELECT guidance FROM life_settings WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        rows = db.execute(
            """SELECT id, sent_at, normalized_text FROM source_messages
            WHERE project_id = ? AND speaker = ? AND sent_at IS NOT NULL
            ORDER BY sent_at""",
            (project_id, project.get("target_speaker") or ""),
        ).fetchall()
    if not setting or not setting["guidance"].strip():
        raise HTTPException(status_code=409, detail="尚未填写虚拟生活设定")
    routines = _extract_routines([dict(row) for row in rows], timezone)
    routine_context = [
        {
            "label": item["label"],
            "typical_time": item["typical_time"],
            "scope": item["scope"],
            "evidence_count": item["evidence_count"],
            "independent_days": item["independent_days"],
        }
        for item in routines
        if item["usable"] and item.get("predictable", True)
    ]
    system_prompt = """你是虚拟人物的每日生活编排器。用户已经明确授权你在其设定范围内
创作虚拟日常。聊天证据是人物习惯参考，用户设定是更高优先级的虚拟世界规则。

请生成自然、有变化但不过分戏剧化的一天。可以在空闲时间安排陪家里的猫、摸鱼、
看视频、打游戏、临时出门、见朋友、逛街、吃饭、KTV等普通生活；不要每天塞满所有
活动。允许偶尔赖床或轻微短暂不舒服，但通常保持正常。禁止严重疾病、事故、犯罪、
危险行为、重大消费、真实人物隐私或会改变现实关系的虚构。

只输出JSON：
{"summary":"","events":[{"start_time":"09:00","end_time":"10:00",
"activity":"","location":"","mood":"","condition":"正常",
"energy":60,"hunger":30,"sleepiness":20,"stress":20}]}
events必须按时间排列，6到12项，不重叠。所有内容都是虚拟世界创作，不是真实经历。"""
    user_prompt = (
        f"人物：{project['display_name']}\n"
        f"日期：{local.date().isoformat()}，星期{'一二三四五六日'[local.weekday()]}\n"
        f"用户设定：\n{setting['guidance']}\n\n"
        f"聊天证据中的稳定作息：\n"
        f"{json.dumps(routine_context, ensure_ascii=False)}"
    )
    payload, _ = deepseek_json(system_prompt, user_prompt, max_tokens=3000)
    events = []
    last_end = "00:00"
    for item in payload.get("events", [])[:12]:
        if not isinstance(item, dict):
            continue
        start = _valid_time(item.get("start_time"))
        end = _valid_time(item.get("end_time"))
        activity = str(item.get("activity") or "").strip()
        if (
            not start or not end or not activity
            or start >= end or start < last_end
        ):
            continue
        events.append({
            "start_time": start,
            "end_time": end,
            "activity": activity[:120],
            "location": str(item.get("location") or "未说明")[:80],
            "mood": str(item.get("mood") or "平静")[:80],
            "condition": str(item.get("condition") or "正常")[:80],
            "energy": _number(item.get("energy")),
            "hunger": _number(item.get("hunger")),
            "sleepiness": _number(item.get("sleepiness")),
            "stress": _number(item.get("stress")),
        })
        last_end = end
    events.sort(key=lambda item: item["start_time"])
    if len(events) < 3:
        raise HTTPException(status_code=502, detail="AI没有生成足够完整的今日计划")
    plan = {
        "summary": str(payload.get("summary") or "今天的虚拟生活")[:300],
        "events": events,
    }
    with connect() as db:
        db.execute(
            """INSERT INTO life_daily_plans (
            id, project_id, plan_date, timezone, guidance, plan_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, plan_date) DO UPDATE SET
            id=excluded.id, timezone=excluded.timezone,
            guidance=excluded.guidance, plan_json=excluded.plan_json,
            created_at=excluded.created_at""",
            (
                new_id(),
                project_id,
                local.date().isoformat(),
                safe_name,
                setting["guidance"],
                json.dumps(plan, ensure_ascii=False),
                now(),
            ),
        )
    return plan


def _plan_event(
    plan_row: Optional[dict[str, Any]], local: datetime
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    if not plan_row:
        return None, []
    minute = local.hour * 60 + local.minute
    current = None
    events = []
    for index, item in enumerate(plan_row["plan"]["events"]):
        start_hour, start_minute = (
            int(part) for part in item["start_time"].split(":")
        )
        end_hour, end_minute = (
            int(part) for part in item["end_time"].split(":")
        )
        start_value = start_hour * 60 + start_minute
        end_value = end_hour * 60 + end_minute
        if start_value <= minute < end_value:
            current = item
        events.append({
            "id": f"{plan_row['id']}:{index}",
            "event_type": "creative_plan",
            "title": item["activity"],
            "description": "由用户生活设定授权AI创作的虚拟活动。",
            "location": item["location"],
            "started_at": local.replace(
                hour=start_hour, minute=start_minute, second=0, microsecond=0
            ).isoformat(),
            "source": "user_guided_ai",
            "confidence": 1.0,
            "evidence_message_ids": [],
            "basis": "用户生活设定 + 聊天记录中的稳定作息",
            "end_time": item["end_time"],
        })
    return current, events


def get_life_snapshot(
    project_id: str,
    timezone_name: Optional[str] = None,
    at: Optional[datetime] = None,
) -> dict[str, Any]:
    timezone, safe_name = _timezone(timezone_name)
    local = (at or datetime.now(timezone)).astimezone(timezone)
    minute = local.hour * 60 + local.minute
    with connect() as db:
        project = db.execute(
            "SELECT target_speaker FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        target = project["target_speaker"] if project else None
        setting = db.execute(
            "SELECT guidance FROM life_settings WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        plan_row = row_to_dict(
            db.execute(
                """SELECT * FROM life_daily_plans
                WHERE project_id = ? AND plan_date = ?""",
                (project_id, local.date().isoformat()),
            ).fetchone()
        )
        rows = db.execute(
            """SELECT id, sent_at, normalized_text FROM source_messages
            WHERE project_id = ? AND speaker = ? AND sent_at IS NOT NULL
            ORDER BY sent_at""",
            (project_id, target or ""),
        ).fetchall()
    messages = [dict(row) for row in rows]
    routines = _extract_routines(messages, timezone)
    recent = _recent_state(messages, local, timezone)
    projected = _projected_activity(routines, minute, local.weekday())
    if projected:
        activity = f"根据历史，这个时段可能在{projected['label']}"
        location = "未知"
        confidence = projected["confidence"]
        basis = projected["basis"]
        activity_code = projected["kind"]
        evidence_ids = projected["evidence_message_ids"]
        activity_started_at = local.replace(
            hour=projected["typical_minute"] // 60,
            minute=projected["typical_minute"] % 60,
            second=0,
            microsecond=0,
        ).isoformat()
    else:
        activity = "聊天记录不足以推断此刻在做什么"
        location = "未知"
        confidence = 0.0
        basis = "没有找到至少跨 3 个日期重复出现、且时间接近当前时段的直接表达。"
        activity_code = "unknown"
        evidence_ids = []
        activity_started_at = local.isoformat(timespec="minutes")
    current_plan, creative_events = _plan_event(plan_row, local)
    if current_plan:
        activity = current_plan["activity"]
        location = current_plan["location"]
        confidence = 1.0
        basis = "由你填写的生活设定授权AI创作，并参考聊天记录中的稳定作息。"
        activity_code = "creative_plan"
        evidence_ids = []
        start_hour, start_minute = (
            int(part) for part in current_plan["start_time"].split(":")
        )
        activity_started_at = local.replace(
            hour=start_hour, minute=start_minute, second=0, microsecond=0
        ).isoformat()
        recent.update({
            "mood": current_plan["mood"],
            "condition": current_plan["condition"],
            "energy": current_plan["energy"],
            "hunger": current_plan["hunger"],
            "sleepiness": current_plan["sleepiness"],
            "stress": current_plan["stress"],
        })
    simulated_at = local.isoformat(timespec="seconds")
    created_at = now()
    with connect() as db:
        db.execute(
            "DELETE FROM life_events WHERE project_id = ?", (project_id,)
        )
        for routine in routines:
            if (
                not _routine_applies(routine, routines, local.weekday())
                or routine["typical_minute"] > minute
            ):
                continue
            event_time = local.replace(
                hour=routine["typical_minute"] // 60,
                minute=routine["typical_minute"] % 60,
                second=0,
                microsecond=0,
            )
            db.execute(
                """INSERT INTO life_events (
                id, project_id, event_key, event_type, title, description,
                location, started_at, source, confidence,
                evidence_message_ids_json, basis, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '未知', ?,
                'evidence_inference', ?, ?, ?, ?)""",
                (
                    new_id(),
                    project_id,
                    f"{local.date().isoformat()}:{routine['kind']}",
                    routine["kind"],
                    f"约 {_clock_text(routine['typical_minute'])} 可能{routine['label']}",
                    "这是根据重复出现的聊天记录推演的时间窗口，并非真实活动记录。",
                    event_time.isoformat(),
                    routine["confidence"],
                    json.dumps(routine["evidence_message_ids"]),
                    routine["basis"],
                    created_at,
                ),
            )
        db.execute(
            """INSERT INTO life_states (
            project_id, timezone, activity, location, mood, condition,
            energy, hunger, sleepiness, health, stress,
            last_simulated_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
            timezone=excluded.timezone, activity=excluded.activity,
            location=excluded.location, mood=excluded.mood,
            condition=excluded.condition, energy=excluded.energy,
            hunger=excluded.hunger, sleepiness=excluded.sleepiness,
            health=excluded.health, stress=excluded.stress,
            last_simulated_at=excluded.last_simulated_at,
            updated_at=excluded.updated_at""",
            (
                project_id,
                safe_name,
                activity,
                location,
                recent["mood"],
                recent["condition"],
                recent["energy"] or 0,
                recent["hunger"] or 0,
                recent["sleepiness"] or 0,
                recent["health"] or 0,
                recent["stress"] or 0,
                simulated_at,
                created_at,
            ),
        )
        state = row_to_dict(
            db.execute(
                "SELECT * FROM life_states WHERE project_id = ?", (project_id,)
            ).fetchone()
        )
        events = [
            row_to_dict(row)
            for row in db.execute(
                """SELECT * FROM life_events WHERE project_id = ?
                ORDER BY started_at""",
                (project_id,),
            ).fetchall()
        ]
    if creative_events:
        events = creative_events
    for key in ("energy", "hunger", "sleepiness", "health", "stress"):
        state[key] = recent[key]
    state.update({
        "activity_code": activity_code,
        "activity_started_at": activity_started_at,
        "confidence": confidence,
        "basis": basis,
        "evidence_message_ids": evidence_ids,
        "recent_evidence_message_ids": recent["evidence_message_ids"],
    })
    return {
        "state": state,
        "events": events,
        "routine_profile": routines,
        "guidance": setting["guidance"] if setting else "",
        "daily_plan": plan_row["plan"] if plan_row else None,
        "date": local.date().isoformat(),
        "is_simulated": True,
        "notice": (
            "今天的日程由你填写的设定授权AI创作，并参考聊天证据；"
            "它属于虚拟世界，不是现实人物的真实经历。"
            if plan_row else
            "所有日常均由聊天记录中的直接表达推演；证据不足会明确显示未知，"
            "不会使用默认上班、吃饭或睡眠作息。"
        ),
    }
