from types import SimpleNamespace
from unittest.mock import patch

from app.deepseek_service import deepseek_json, deepseek_reply, deepseek_status


PROJECT = {"display_name": "小林", "relationship_type": "老朋友"}


def test_reports_unconfigured_without_exposing_key() -> None:
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
        result = deepseek_status()

    assert result["configured"] is False
    assert "api_key" not in result


def test_returns_none_when_key_is_missing() -> None:
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
        assert deepseek_reply("你好", PROJECT, [], []) is None


def test_calls_deepseek_without_real_network_request() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"reply":"在呢。","tone":"随意","expression":"抬了下眼"}'
        ))]
    )
    with patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        },
        clear=False,
    ), patch("app.deepseek_service.OpenAI") as client_class:
        client_class.return_value.chat.completions.create.return_value = response
        result = deepseek_reply(
            "你好",
            PROJECT,
            [],
            [],
            {
                "traits": [{"name": "情绪直白", "value": "会直接说出不满"}],
                "relationship": {
                    "affect_profile": {"baseline": "平时轻松直接"}
                },
            },
            {"period": "晚上", "weather": {"condition": "小雨"}},
            [
                {"role": "user", "content": "你刚刚不是说在睡觉吗"},
                {"role": "assistant", "content": "刚醒。"},
            ],
        )

    assert result == {
        "reply": "在呢。",
        "tone": "随意",
        "expression": "抬了下眼",
    }
    client_class.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.deepseek.com",
    )
    create = client_class.return_value.chat.completions.create
    assert create.call_args.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert create.call_args.kwargs["response_format"] == {
        "type": "json_object"
    }
    system_prompt = create.call_args.kwargs["messages"][0]["content"]
    sent_messages = create.call_args.kwargs["messages"]
    assert sent_messages[1:] == [
        {"role": "user", "content": "你刚刚不是说在睡觉吗"},
        {"role": "assistant", "content": "刚醒。"},
        {"role": "user", "content": "你好"},
    ]
    assert "情绪反应机制" in system_prompt
    assert "平时轻松直接" in system_prompt
    assert "心理咨询师" in system_prompt
    assert "小雨" in system_prompt
    assert '"expression"' in system_prompt
    assert "对话连续性是最高优先级" in system_prompt
    assert "不得覆盖本轮对话里已经说过的事实" in system_prompt


def test_json_retries_once_after_incomplete_response() -> None:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        prompt_cache_hit_tokens=2,
        prompt_cache_miss_tokens=8,
    )
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"broken":'))],
            usage=usage,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=usage,
        ),
    ]
    with patch.dict(
        "os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=False
    ), patch("app.deepseek_service.OpenAI") as client_class:
        client_class.return_value.chat.completions.create.side_effect = responses
        payload, totals = deepseek_json("输出 JSON", "分析内容", max_tokens=100)

    assert payload == {"ok": True}
    assert totals == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "cache_hit_tokens": 4,
        "cache_miss_tokens": 16,
    }
    calls = client_class.return_value.chat.completions.create.call_args_list
    assert calls[0].kwargs["max_tokens"] == 100
    assert calls[1].kwargs["max_tokens"] == 200
    assert calls[0].kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_json_accepts_markdown_code_fence() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='```json\n{"ok": true}\n```')
            )
        ],
        usage=None,
    )
    with patch.dict(
        "os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=False
    ), patch("app.deepseek_service.OpenAI") as client_class:
        client_class.return_value.chat.completions.create.return_value = response
        payload, _ = deepseek_json("输出 JSON", "分析内容")

    assert payload == {"ok": True}
