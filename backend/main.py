import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from supabase import create_client
from dotenv import load_dotenv
from persona import SYSTEM_PROMPT
from tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS

load_dotenv()

app = FastAPI()

# Allow the frontend (any origin) to call this backend.
# You can tighten this later to just your Vercel URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not all([GROQ_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError(
        "Missing required environment variables. "
        "Set GROQ_API_KEY, SUPABASE_URL, SUPABASE_KEY."
    )

groq_client = Groq(api_key=GROQ_API_KEY)
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MODEL_NAME = "llama-3.3-70b-versatile"
FALLBACK_MODEL_NAME = "llama-3.1-8b-instant"  # lighter/faster free model, used if primary fails
HISTORY_LIMIT = 20  # how many past messages to feed back as context


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class RenameRequest(BaseModel):
    title: str


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Zeal Co-Pilot backend is running"}


@app.get("/conversations")
def list_conversations():
    result = (
        sb.table("conversations")
        .select("id,title,created_at")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"conversations": result.data}


@app.post("/conversations")
def new_conversation():
    result = sb.table("conversations").insert({}).execute()
    return {"conversation_id": result.data[0]["id"]}


@app.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, req: RenameRequest):
    title = req.title.strip()[:60]
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    sb.table("conversations").update({"title": title}).eq("id", conversation_id).execute()
    return {"id": conversation_id, "title": title}


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    # messages table has ON DELETE CASCADE on conversation_id, so this
    # removes the conversation's messages automatically too.
    sb.table("conversations").delete().eq("id", conversation_id).execute()
    return {"deleted": True, "id": conversation_id}


@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str):
    result = (
        sb.table("messages")
        .select("role,content,created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {"messages": result.data}


def _run_completion(messages, model, with_tools=True):
    """Single call to Groq. Raises on failure so the caller can fall back."""
    kwargs = dict(model=model, messages=messages, temperature=0.4, max_tokens=2000)
    if with_tools:
        kwargs["tools"] = TOOL_DEFINITIONS
        kwargs["tool_choice"] = "auto"
    return groq_client.chat.completions.create(**kwargs)


def _get_reply(messages, model):
    """
    Runs the tool-calling loop for one model. Any single tool failure is caught
    and fed back to the model as an error string (never crashes the request).
    Returns the final text reply, or raises if the model call itself fails.
    """
    MAX_TOOL_ROUNDS = 4
    for _ in range(MAX_TOOL_ROUNDS):
        completion = _run_completion(messages, model)
        msg = completion.choices[0].message

        if not msg.tool_calls:
            return msg.content

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            fn = TOOL_FUNCTIONS.get(fn_name)
            try:
                result = fn(args) if fn else f"Unknown tool: {fn_name}"
            except Exception as tool_error:
                # A single tool failing should never crash the whole reply —
                # tell the model it failed so it can respond gracefully instead.
                result = f"Tool '{fn_name}' failed: {str(tool_error)}"

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    # Ran out of tool-call rounds — force a plain final answer, no more tools
    completion = _run_completion(messages, model, with_tools=False)
    return completion.choices[0].message.content


@app.post("/chat")
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # 1. Fetch conversation history
    history = (
        sb.table("messages")
        .select("role,content")
        .eq("conversation_id", req.conversation_id)
        .order("created_at")
        .limit(HISTORY_LIMIT)
        .execute()
        .data
    )
    is_first_message = len(history) == 0

    # 2. Save the user's new message
    sb.table("messages").insert(
        {"conversation_id": req.conversation_id, "role": "user", "content": req.message}
    ).execute()

    # If this is the first message in the conversation, use it as the sidebar title
    if is_first_message:
        clean = " ".join(req.message.strip().split())  # collapse newlines/extra spaces
        title = clean[:57] + "..." if len(clean) > 57 else clean
        sb.table("conversations").update({"title": title}).eq("id", req.conversation_id).execute()

    # 3. Build the message list for the model
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": req.message})

    # 4. Try the primary model first; fall back to a lighter free model on failure
    # instead of surfacing a raw error to the user.
    reply = None
    last_error = None
    for model in (MODEL_NAME, FALLBACK_MODEL_NAME):
        try:
            # Each attempt needs its own copy of messages since the tool loop mutates it
            reply = _get_reply(list(messages), model)
            break
        except Exception as e:
            last_error = e
            continue

    if reply is None:
        reply = (
            "I couldn't reach the AI model just now — this is usually a temporary "
            "rate limit or connectivity issue. Please try again in a moment."
        )
        # Still log the real error server-side for debugging
        print(f"Both models failed: {last_error}")

    # 5. Save the assistant's reply
    sb.table("messages").insert(
        {
            "conversation_id": req.conversation_id,
            "role": "assistant",
            "content": reply,
        }
    ).execute()

    return {"reply": reply}
