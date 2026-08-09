from unittest.mock import patch

from app.runtime_context import build_runtime_context, fetch_weather


def test_runtime_context_works_without_location() -> None:
    context = build_runtime_context("Asia/Shanghai")

    assert context["timezone"] == "Asia/Shanghai"
    assert context["period"] in {"深夜", "早晨", "上午", "中午", "下午", "晚上"}
    assert context["location_authorized"] is False
    assert "weather" not in context


def test_weather_uses_reduced_coordinate_precision() -> None:
    response = patch("app.runtime_context.httpx.get").start()
    response.return_value.json.return_value = {
        "current": {
            "temperature_2m": 26.1,
            "apparent_temperature": 27.0,
            "precipitation": 0,
            "weather_code": 2,
            "wind_speed_10m": 8.4,
        }
    }
    try:
        weather = fetch_weather(31.234567, 121.456789)
    finally:
        patch.stopall()

    assert weather == {
        "condition": "多云",
        "temperature_c": 26.1,
        "feels_like_c": 27.0,
        "precipitation_mm": 0,
        "wind_kmh": 8.4,
    }
    params = response.call_args.kwargs["params"]
    assert params["latitude"] == 31.23
    assert params["longitude"] == 121.46
