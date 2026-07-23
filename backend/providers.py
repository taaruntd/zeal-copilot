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
# Groq's vision-capable model lineup changes often (they deprecated the
# previous one, llama-4-scout, in June 2026). Configurable via env var so a
# future swap doesn't need a code change — just update GROQ_VISION_MODEL in
# Render's Environment tab and redeploy.
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
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
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # Surface OpenRouter's actual error message instead of the generic
        # "Not Found for url" text, which hides the real reason. Keep raising
        # the same HTTPError type (with .response attached) so the 404-retry
        # logic in get_reply() still works.
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            detail = resp.text
        e.args = (f"{e.args[0]} — {detail}",)
        raise
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    # Google's newer "Auth key" format (starts with AQ.) must be sent as a
    # header, not the old ?key= URL query param the API used to require.
    resp = requests.post(
        url,
        json=body,
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    if not resp.ok:
        # Surface Google's actual error message (e.g. "API not enabled for this
        # project", "model retired") instead of requests' generic "Not Found for
        # url" text, which hides the real reason.
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"Gemini API error ({resp.status_code}): {detail}")
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
        try:
            return _run_tool_loop(_openrouter_call, OPENROUTER_MODEL, list(messages))
        except requests.exceptions.HTTPError as e:
            # Some free OpenRouter models don't support tool/function calling at
            # all and return 404 "No endpoints found that support tool use" the
            # moment tools are attached. Fall back to a plain call with no tools
            # rather than failing outright — live-data tools just won't fire.
            if e.response is not None and e.response.status_code == 404:
                result = _openrouter_call(list(messages), OPENROUTER_MODEL, with_tools=False)
                return result["content"]
            raise

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
