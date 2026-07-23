import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
from persona import SYSTEM_PROMPT
import providers

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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables. Set SUPABASE_URL, SUPABASE_KEY.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HISTORY_LIMIT = 20  # how many past messages to feed back as context
VALID_PROVIDERS = {"groq", "openrouter", "gemini"}


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    provider: str = "groq"
    image: Optional[str] = None  # base64 data URL, e.g. "data:image/png;base64,...."


class RenameRequest(BaseModel):
    title: str


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Zeal Co-Pilot backend is running"}


@app.get("/providers")
def list_providers():
    """Tells the frontend which providers actually have API keys configured,
    so it can disable/hide options that won't work."""
    available = providers.available_providers()
    return {
        "providers": [
            {"id": "groq", "label": "Groq (Llama) — with live data tools", "available": available["groq"]},
            {"id": "openrouter", "label": "OpenRouter — with live data tools", "available": available["openrouter"]},
            {"id": "gemini", "label": "Gemini — no live data tools", "available": available["gemini"]},
        ]
    }


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


@app.post("/chat")
def chat(req: ChatRequest):
    if not req.message.strip() and not req.image:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    provider = req.provider if req.provider in VALID_PROVIDERS else "groq"

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

    # 2. Save the user's new message. Images aren't persisted to the database
    # (keeps rows small and avoids needing file storage) — just a text marker
    # so the conversation history still reads sensibly on reload.
    stored_user_content = req.message.strip()
    if req.image:
        stored_user_content = (stored_user_content + " " if stored_user_content else "") + "[Image attached]"

    sb.table("messages").insert(
        {"conversation_id": req.conversation_id, "role": "user", "content": stored_user_content}
    ).execute()

    # If this is the first message in the conversation, use it as the sidebar title
    if is_first_message:
        clean = " ".join(stored_user_content.strip().split())  # collapse newlines/extra spaces
        title = clean[:57] + "..." if len(clean) > 57 else clean
        sb.table("conversations").update({"title": title}).eq("id", req.conversation_id).execute()

    # 3. Get the reply — image messages always go through Groq's vision model,
    # regardless of which text provider is selected (only Groq is wired for
    # images right now). Otherwise use whichever provider was chosen.
    try:
        if req.image:
            reply = providers.get_vision_reply(SYSTEM_PROMPT, history, req.message, req.image)
        else:
            reply = providers.get_reply(provider, SYSTEM_PROMPT, history, req.message)
    except Exception as e:
        print(f"Provider '{provider}' (image={bool(req.image)}) failed: {e}")
        reply = (
            "I couldn't process that just now — this is usually a temporary rate "
            "limit, missing API key, or connectivity issue. Try again in a moment."
        )

    # 4. Save the assistant's reply
    sb.table("messages").insert(
        {"conversation_id": req.conversation_id, "role": "assistant", "content": reply}
    ).execute()

    return {"reply": reply, "provider": provider}
