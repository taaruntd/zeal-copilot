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
HISTORY_LIMIT = 20  # how many past messages to feed back as context


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Zeal Co-Pilot backend is running"}


@app.post("/conversations")
def new_conversation():
    result = sb.table("conversations").insert({}).execute()
    return {"conversation_id": result.data[0]["id"]}


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

    # 2. Save the user's new message
    sb.table("messages").insert(
        {
            "conversation_id": req.conversation_id,
            "role": "user",
            "content": req.message,
        }
    ).execute()

    # 3. Build the message list for the model
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": req.message})

    # 4. Call Groq (Llama 3.3 70B), allowing it to call live-data tools.
    # The loop lets the model: ask for a tool -> we run it -> feed result back ->
    # model either asks for another tool or gives a final answer.
    try:
        MAX_TOOL_ROUNDS = 4
        for _ in range(MAX_TOOL_ROUNDS):
            completion = groq_client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.4,
                max_tokens=2000,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
            msg = completion.choices[0].message

            if not msg.tool_calls:
                reply = msg.content
                break

            # Model wants to call one or more tools — run them and feed results back
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
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
                result = fn(args) if fn else f"Unknown tool: {fn_name}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    }
                )
        else:
            # Ran out of rounds — force a final answer with no more tool calls
            completion = groq_client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.4,
                max_tokens=2000,
            )
            reply = completion.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(e)}")

    # 5. Save the assistant's reply
    sb.table("messages").insert(
        {
            "conversation_id": req.conversation_id,
            "role": "assistant",
            "content": reply,
        }
    ).execute()

    return {"reply": reply}
