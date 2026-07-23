"""
Multi-provider LLM layer for Zeal Co-Pilot.

Groq and OpenRouter both speak the same OpenAI-style chat format, including
function/tool calling, so they share one tool-calling loop. Gemini uses a
different API shape entirely — it's wired up as a plain conversational model
(no live-data tools) to keep this addition contained.
"""

import os
import json
import requests
from groq import Groq
from tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK_MODEL = "llama-3.1-8b-instant"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # multimodal, handles images
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

MAX_TOOL_ROUNDS = 4


def available_providers():
    """Which providers are actually configured (have an API key set)."""
    return {
        "groq": bool(GROQ_API_KEY),
        "openrouter": bool(OPENROUTER_API_KEY),
        "gemini": bool(GEMINI_API_KEY),
    }


# ---------------------------------------------------------------------------
# Shared OpenAI-style tool-calling loop (used by both Groq and OpenRouter)
# ---------------------------------------------------------------------------

def _groq_call(messages, model, with_tools=True):
    kwargs = dict(model=model, messages=messages, temperature=0.4, max_tokens=2000)
    if with_tools:
        kwargs["tools"] = TOOL_DEFINITIONS
        kwargs["tool_choice"] = "auto"
    completion = groq_client.chat.completions.create(**kwargs)
    msg = completion.choices[0].message
    return {
        "content": msg.content,
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (msg.tool_calls or [])
        ],
    }


def _openrouter_call(messages, model, with_tools=True):
    body = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 2000}
    if with_tools:
        body["tools"] = TOOL_DEFINITIONS
        body["tool_choice"] = "auto"
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls") or []
    return {
        "content": msg.get("content"),
        "tool_calls": [
            {"id": tc["id"], "name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
            for tc in tool_calls
        ],
    }


def _run_tool_loop(call_fn, model, messages):
    """
    Generic tool-calling loop shared by any OpenAI-style provider.
    call_fn(messages, model, with_tools) -> {"content": str, "tool_calls": [...]}
    """
    for _ in range(MAX_TOOL_ROUNDS):
        result = call_fn(messages, model, with_tools=True)

        if not result["tool_calls"]:
            return result["content"]

        messages.append(
            {
                "role": "assistant",
                "content": result["content"] or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in result["tool_calls"]
                ],
            }
        )

        for tc in result["tool_calls"]:
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            fn = TOOL_FUNCTIONS.get(tc["name"])
            try:
                tool_result = fn(args) if fn else f"Unknown tool: {tc['name']}"
            except Exception as tool_error:
                tool_result = f"Tool '{tc['name']}' failed: {str(tool_error)}"

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(tool_result)})

    # Ran out of tool rounds — force a plain final answer, no more tools
    result = call_fn(messages, model, with_tools=False)
    return result["content"]


# ---------------------------------------------------------------------------
# Vision — image understanding via Groq's multimodal model.
# No tool-calling here (vision + tools together isn't reliably supported),
# and this is used regardless of the selected text provider since only
# Groq is wired for images currently.
# ---------------------------------------------------------------------------

def get_vision_reply(system_prompt, history, user_message, image_data_url):
    if not GROQ_API_KEY:
        raise RuntimeError("Groq is not configured (required for image understanding).")

    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_message or "What's in this image?"},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    )

    completion = groq_client.chat.completions.create(
        model=GROQ_VISION_MODEL,
        messages=messages,
        temperature=0.4,
        max_tokens=2000,
    )
    return completion.choices[0].message.content


# ---------------------------------------------------------------------------
# Gemini — plain conversational call, no tool-calling (different API shape)
# ---------------------------------------------------------------------------

def _gemini_reply(system_prompt, history, user_message):
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    body = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2000},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    resp = requests.post(url, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_reply(provider, system_prompt, history, user_message):
    """
    Dispatches to the chosen provider. Raises on failure so the caller
    (main.py) can decide how to present the error to the user.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_message})

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OpenRouter is not configured (missing OPENROUTER_API_KEY).")
        return _run_tool_loop(_openrouter_call, OPENROUTER_MODEL, list(messages))

    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("Gemini is not configured (missing GEMINI_API_KEY).")
        return _gemini_reply(system_prompt, history, user_message)

    # Default: Groq, with an automatic fallback to a lighter free model
    if not GROQ_API_KEY:
        raise RuntimeError("Groq is not configured (missing GROQ_API_KEY).")
    last_error = None
    for model in (GROQ_MODEL, GROQ_FALLBACK_MODEL):
        try:
            return _run_tool_loop(_groq_call, model, list(messages))
        except Exception as e:
            last_error = e
            continue
    raise last_error
