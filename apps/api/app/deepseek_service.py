import json
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def deepseek_status() -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
    }


def deepseek_reply(
    message: str,
    project: dict[str, Any],
    examples: list[dict[str, Any]],
    memories: list[dict[str, Any]],
    version: Optional[dict[str, Any]] = None,
    runtime_context: Optional[dict[str, Any]] = None,
    conversation_history: Optional[list[dict[str, str]]] = None,
) -> Optional[dict[str, str]]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None

    example_text = "\n".join(
        f"用户：{item['context_text']}\n{project['display_name']}：{item['reply_text']}"
        for item in examples[:8]
    )
    memory_text = "\n".join(f"- {item['content']}" for item in memories[:8])
    relationship = (version or {}).get("relationship") or {}
    emotional_profile = {
        "稳定性格": (version or {}).get("traits", [])[:8],
        "情绪基线": relationship.get("affect_profile", {}),
        "情绪触发与恢复": relationship.get("emotional_patterns", [])[:6],
        "关系模式": relationship.get("relationship_patterns", [])[:5],
        "冲突与修复": relationship.get("conflict_patterns", [])[:4],
        "需求与边界": relationship.get("needs_and_boundaries", [])[:5],
    }
    emotional_profile_text = json.dumps(
        emotional_profile, ensure_ascii=False, separators=(",", ":")
    )
    runtime_context_text = json.dumps(
        runtime_context or {}, ensure_ascii=False, separators=(",", ":")
    )
    system_prompt = f"""你正在扮演一个基于授权聊天记录生成的虚拟人格。
人格名称：{project['display_name']}
与用户关系：{project['relationship_type']}

目标是复现这个人的情绪反应机制、性格和关系态度，而不是套用历史回答模板。
收到用户消息后，请先在内部完成两步，但只输出最终回复：
1. 根据稳定人格、情绪触发、当前关系和用户消息，推断此人此刻最可能的情绪、
   强度、需求与表达方式。
2. 从这个心理状态自然地说话。允许冷淡、烦躁、调侃、回避或简短，不要默认变成
   温柔的心理咨询师，也不要解释你的分析过程。

对话连续性是最高优先级：
- 先理解最近对话中“刚刚、那个、然后呢、为什么”等指代，再回答当前这句话。
- 已经在本轮对话里说过的事情必须保持一致，不能被背景资料或虚拟日程覆盖。
- 回复应像即时聊天，通常一句或两句短句；不要每次都总结、解释、反问或汇报状态。
- 不要为了“像这个人”而每句堆口头禅、脏话、网络梗或动作描写。人格应自然体现在
  关注点、措辞和情绪反应里；能用普通短句回答时，就用普通短句。

情绪与人格模型：
{emotional_profile_text}

当前环境：
{runtime_context_text}
环境只用于理解时间、作息和天气。不要生硬复述；用户没提到时也不要强行谈天气。
不得根据大致位置推断住址、单位或行动轨迹。
virtual_life 是系统明确标记的虚拟生活推演。回复要与其中的当前活动、精力、困意
和心情不冲突，但它只是低优先级背景。除非用户明确问“在干嘛、刚刚做了什么”等
相关问题，否则不要主动提起；它也不得覆盖本轮对话里已经说过的事实。不得说成
现实人物真正发生过的事实，也不要机械汇报所有数值。
其中为“未知”或 null 的字段没有聊天证据，禁止自行补全。

长期记忆：
{memory_text or "- 暂无"}

下面的真实回复只用于校准措辞、长度和口语习惯，不能代替情绪与人格推理：
真实回复范例：
{example_text or "暂无"}

不得声称自己是真实人物，不要编造记忆或当前对话中不存在的事实。
请只输出 JSON 对象：
{{"reply":"实际说出口的话","tone":"此刻的语气，2到12字",
"expression":"适合文字界面展示的神态或小动作，2到20字"}}
expression 是虚拟角色的非语言表达，不得声称真实触碰用户、真实看到用户或身处
用户所在地点。不要用 Markdown，不要输出分析过程。
"""
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        )
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in (conversation_history or [])[-20:]
            if item.get("role") in {"user", "assistant"}
            and str(item.get("content") or "").strip()
        ]
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": message},
            ],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            stream=False,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="DeepSeek 调用失败，请检查 API Key、余额和网络连接",
        ) from error
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="DeepSeek 没有返回有效回复")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail="DeepSeek 返回的回复格式不正确") from error
    reply = str(payload.get("reply") or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="DeepSeek 没有返回有效回复")
    return {
        "reply": reply,
        "tone": str(payload.get("tone") or "").strip()[:40],
        "expression": str(payload.get("expression") or "").strip()[:80],
    }


def deepseek_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 6000,
) -> tuple[dict[str, Any], dict[str, int]]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=409, detail="尚未配置 DeepSeek API Key")
    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        )
        for attempt in range(2):
            response = client.chat.completions.create(
                model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_prompt if attempt == 0 else (
                            f"{user_prompt}\n\n请重新输出一个完整、精炼的 JSON 对象，"
                            "不要使用 Markdown 代码块。"
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens if attempt == 0 else max_tokens * 2,
                extra_body={"thinking": {"type": "disabled"}},
                stream=False,
            )
            usage = getattr(response, "usage", None)
            usage_totals["prompt_tokens"] += int(
                getattr(usage, "prompt_tokens", 0) or 0
            )
            usage_totals["completion_tokens"] += int(
                getattr(usage, "completion_tokens", 0) or 0
            )
            usage_totals["cache_hit_tokens"] += int(
                getattr(usage, "prompt_cache_hit_tokens", 0) or 0
            )
            usage_totals["cache_miss_tokens"] += int(
                getattr(usage, "prompt_cache_miss_tokens", 0) or 0
            )
            content = (response.choices[0].message.content or "").strip()
            if content.startswith("```"):
                content = content.removeprefix("```json").removeprefix("```")
                content = content.removesuffix("```").strip()
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                if attempt == 0:
                    continue
                raise HTTPException(
                    status_code=502,
                    detail="DeepSeek 连续两次返回了不完整的 JSON，请稍后续传",
                )
            if not isinstance(payload, dict):
                raise HTTPException(
                    status_code=502, detail="DeepSeek 返回的分析格式不正确"
                )
            return payload, usage_totals
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="DeepSeek 调用失败，请检查 API Key、余额和网络连接",
        ) from error
    raise HTTPException(status_code=502, detail="DeepSeek 没有返回有效 JSON")
