import os
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
from persona import SYSTEM_PROMPT
import providers
import voice

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


class TranslateRequest(BaseModel):
    language: str
    mode: str = "structured"  # "structured" (summary) or "transcript" (verbatim)


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


# ---------------------------------------------------------------------------
# Voice Notes — a separate feature from regular chat. Record a memo, get it
# transcribed (Whisper, handles Hindi/mixed-language speech) and structured
# into a short note. Has its own table, its own endpoints, nothing shared
# with the conversations/messages tables above.
# ---------------------------------------------------------------------------

@app.get("/voice-notes")
def list_voice_notes():
    result = (
        sb.table("voice_notes")
        .select("id,title,transcript,structured,created_at")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"voice_notes": result.data}


@app.post("/voice-notes")
async def create_voice_note(audio: UploadFile = File(...)):
    file_bytes = await audio.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="No audio received")

    try:
        result = voice.process_voice_note(file_bytes, audio.filename or "audio.webm")
    except Exception as e:
        print(f"Voice note processing failed: {e}")
        raise HTTPException(
            status_code=502,
            detail="Couldn't process that recording — try again, or check it wasn't silent/too short.",
        )

    # Pull a short title out of the structured note's TITLE: line, falling
    # back to the first few words of the transcript if that's missing.
    title = None
    for line in result["structured"].splitlines():
        if line.strip().upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            break
    if not title:
        title = " ".join(result["transcript"].split()[:8])

    row = {
        "title": title[:80],
        "transcript": result["transcript"],
        "structured": result["structured"],
    }
    saved = sb.table("voice_notes").insert(row).execute()
    return saved.data[0]


@app.patch("/voice-notes/{note_id}")
def rename_voice_note(note_id: str, req: RenameRequest):
    title = req.title.strip()[:80]
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    sb.table("voice_notes").update({"title": title}).eq("id", note_id).execute()
    return {"id": note_id, "title": title}


@app.delete("/voice-notes/{note_id}")
def delete_voice_note(note_id: str):
    sb.table("voice_notes").delete().eq("id", note_id).execute()
    return {"deleted": True, "id": note_id}


@app.post("/voice-notes/{note_id}/translate")
def translate_voice_note(note_id: str, req: TranslateRequest):
    """Translates a note's structured summary OR full transcript into any
    language, on demand. Does NOT overwrite the saved original — this is a
    view-only translation returned to the frontend each time it's requested."""
    language = req.language.strip()
    if not language:
        raise HTTPException(status_code=400, detail="Language cannot be empty")
    mode = req.mode if req.mode in ("structured", "transcript") else "structured"

    result = (
        sb.table("voice_notes")
        .select("structured,transcript")
        .eq("id", note_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Voice note not found")

    try:
        if mode == "transcript":
            translated = voice.translate_text(result.data[0]["transcript"], language)
        else:
            translated = voice.translate_structured(result.data[0]["structured"], language)
    except Exception as e:
        print(f"Translation failed (mode={mode}): {e}")
        raise HTTPException(status_code=502, detail="Translation failed — try again.")

    return {"translated": translated, "language": language, "mode": mode}
