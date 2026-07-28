"""
Voice Notes — a feature separate from regular chat.

Flow: record audio -> Groq Whisper transcribes it (supports Hindi and 90+
other languages, including mixed-language speech) -> a lightweight LLM pass
turns the raw transcript into a short structured note (title, key points,
action items) -> both the raw transcript and the structured note are saved.

This deliberately does NOT touch the chat conversation history/tables —
voice notes are their own thing with their own table.
"""

import os
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-large-v3")
STRUCTURE_MODEL = "llama-3.1-8b-instant"  # small/fast model, just for formatting

STRUCTURE_PROMPT = """You turn a raw voice-memo transcript into a short, useful note.
The transcript may be in Hindi, English, a mix of both, or another language —
keep your output in the same language(s) the transcript uses, don't translate
unless the transcript is unclear without it.

Respond in this exact format, nothing else:

TITLE: <a short 5-8 word title for this note>
SUMMARY: <2-4 sentence plain-language summary>
KEY POINTS:
- <point>
- <point>
ACTION ITEMS:
- <item, or write "None" if there aren't any>
"""


def transcribe_audio(file_bytes: bytes, filename: str = "audio.webm") -> str:
    """Sends audio to Groq's Whisper endpoint, returns the raw transcript text."""
    if not groq_client:
        raise RuntimeError("Groq is not configured (required for voice transcription).")
    transcription = groq_client.audio.transcriptions.create(
        file=(filename, file_bytes),
        model=WHISPER_MODEL,
        response_format="text",
    )
    # The SDK returns a plain string when response_format="text"
    return str(transcription).strip()


def structure_transcript(transcript: str) -> str:
    """Turns a raw transcript into a short structured note (title/summary/
    key points/action items) using a small, fast model — this is a simple
    formatting pass, not a full conversation, so no history is needed."""
    if not groq_client:
        raise RuntimeError("Groq is not configured (required for structuring notes).")
    completion = groq_client.chat.completions.create(
        model=STRUCTURE_MODEL,
        messages=[
            {"role": "system", "content": STRUCTURE_PROMPT},
            {"role": "user", "content": transcript},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    return completion.choices[0].message.content


def translate_text(text: str, target_language: str) -> str:
    """Plain verbatim translation — used for the full transcript, where we
    want a faithful word-for-word translation, not a reformatted summary."""
    if not groq_client:
        raise RuntimeError("Groq is not configured (required for translation).")
    completion = groq_client.chat.completions.create(
        model=STRUCTURE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Translate the following text into {target_language}. "
                    "Output only the translation, nothing else — no notes, "
                    "no preamble."
                ),
            },
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=2000,
    )
    return completion.choices[0].message.content


def translate_structured(structured_text: str, target_language: str) -> str:
    """Translates an already-structured note into the requested language,
    keeping the same TITLE/SUMMARY/KEY POINTS/ACTION ITEMS format so the
    frontend can parse it the same way regardless of language."""
    if not groq_client:
        raise RuntimeError("Groq is not configured (required for translation).")
    completion = groq_client.chat.completions.create(
        model=STRUCTURE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Translate the following note into {target_language}. "
                    "Keep the exact same structure — TITLE:, SUMMARY:, "
                    "KEY POINTS:, ACTION ITEMS: — just translate the content "
                    "after each label. Output nothing else."
                ),
            },
            {"role": "user", "content": structured_text},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return completion.choices[0].message.content


def process_voice_note(file_bytes: bytes, filename: str = "audio.webm") -> dict:
    """Full pipeline: transcribe, then structure. Returns both pieces so the
    caller can save whichever it wants (or both)."""
    transcript = transcribe_audio(file_bytes, filename)
    if not transcript:
        raise RuntimeError("Got an empty transcript — the recording may have been silent or too short.")
    structured = structure_transcript(transcript)
    return {"transcript": transcript, "structured": structured}
