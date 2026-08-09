from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


WEATHER_CODES = {
    0: "晴",
    1: "大致晴朗",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴冰雹",
}


def _local_time(timezone_name: Optional[str]) -> tuple[datetime, str]:
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name else ZoneInfo("UTC")
        name = timezone_name or "UTC"
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
        name = "UTC"
    return datetime.now(timezone), name


def _time_period(hour: int) -> str:
    if hour < 5:
        return "深夜"
    if hour < 9:
        return "早晨"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def fetch_weather(latitude: float, longitude: float) -> Optional[dict[str, Any]]:
    # Two decimal places is roughly kilometre-level precision. The exact browser
    # coordinate is neither sent to the weather provider nor returned to the model.
    params = {
        "latitude": round(latitude, 2),
        "longitude": round(longitude, 2),
        "current": (
            "temperature_2m,apparent_temperature,precipitation,"
            "weather_code,wind_speed_10m"
        ),
        "timezone": "auto",
    }
    try:
        response = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            timeout=3.0,
        )
        response.raise_for_status()
        current = response.json().get("current") or {}
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if current.get("temperature_2m") is None:
        return None
    code = int(current.get("weather_code", -1))
    return {
        "condition": WEATHER_CODES.get(code, "天气状况未知"),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "precipitation_mm": current.get("precipitation"),
        "wind_kmh": current.get("wind_speed_10m"),
    }


def build_runtime_context(
    timezone_name: Optional[str],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> dict[str, Any]:
    local, safe_timezone = _local_time(timezone_name)
    context: dict[str, Any] = {
        "local_time": local.isoformat(timespec="minutes"),
        "timezone": safe_timezone,
        "period": _time_period(local.hour),
        "weekday": "一二三四五六日"[local.weekday()],
    }
    if latitude is not None and longitude is not None:
        context["weather"] = fetch_weather(latitude, longitude)
        context["location_authorized"] = True
    else:
        context["location_authorized"] = False
    return context
