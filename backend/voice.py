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
# Translation needs stronger instruction-following than the small model
# reliably gives on messy/slang-heavy transcripts — it was observed adding
# commentary and word-glossaries instead of just translating. Use the same
# solid model the main chat uses.
TRANSLATE_MODEL = "llama-3.3-70b-versatile"

STRUCTURE_PROMPT = """You turn a raw voice-memo transcript into a short, useful note.
The transcript may be in Hindi, English, a mix of both, or another language.

Write the ENTIRE note — TITLE, SUMMARY, KEY POINTS, and ACTION ITEMS — in
English, always, regardless of what language the transcript is in. The
person reading this note may not be able to read Hindi or other scripts, so
the note itself must default to English even though the original spoken
words were in a different language. Translate the meaning faithfully; don't
just transliterate.

(The raw transcript is saved separately in its original language for an
accurate record — you are not being asked to touch that here, only to
produce an English note summarizing it.)

Respond in this exact format, nothing else:

TITLE: <a short 5-8 word title for this note, in English>
SUMMARY: <2-4 sentence plain-language summary, in English>
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
        model=TRANSLATE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a direct translation engine. Translate the user's "
                    f"text into {target_language}.\n\n"
                    "STRICT RULES:\n"
                    "- Output ONLY the translated text. Nothing else.\n"
                    "- Do NOT repeat, quote, or restate the original text.\n"
                    "- Do NOT explain what any word or phrase means.\n"
                    "- Do NOT add a glossary, notes, commentary, or preamble "
                    "like 'here is the translation'.\n"
                    "- Do NOT wrap the output in quotation marks.\n"
                    "- If a word is slang or unclear, translate your best "
                    "interpretation of it directly — do not stop to explain it.\n"
                    "- Your entire response must be the translation itself, "
                    "in the target language, and nothing more."
                ),
            },
            {"role": "user", "content": text},
        ],
        temperature=0,
        max_tokens=2000,
    )
    return completion.choices[0].message.content.strip()


def translate_structured(structured_text: str, target_language: str) -> str:
    """Translates an already-structured note into the requested language,
    keeping the same TITLE/SUMMARY/KEY POINTS/ACTION ITEMS format so the
    frontend can parse it the same way regardless of language."""
    if not groq_client:
        raise RuntimeError("Groq is not configured (required for translation).")
    completion = groq_client.chat.completions.create(
        model=TRANSLATE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a direct translation engine. Translate the following "
                    f"note into {target_language}.\n\n"
                    "STRICT RULES:\n"
                    "- Keep the exact same structure — the labels TITLE:, "
                    "SUMMARY:, KEY POINTS:, ACTION ITEMS: must stay in English "
                    "as section labels; only translate the content after each one.\n"
                    "- Do NOT repeat or quote the original language content "
                    "anywhere in your output.\n"
                    "- Do NOT explain what any word or phrase means.\n"
                    "- Do NOT add a glossary, notes, or commentary of any kind.\n"
                    "- Output ONLY the translated note in the exact same format, "
                    "nothing else before or after it."
                ),
            },
            {"role": "user", "content": structured_text},
        ],
        temperature=0,
        max_tokens=600,
    )
    return completion.choices[0].message.content.strip()


def process_voice_note(file_bytes: bytes, filename: str = "audio.webm") -> dict:
    """Full pipeline: transcribe, then structure. Returns both pieces so the
    caller can save whichever it wants (or both)."""
    transcript = transcribe_audio(file_bytes, filename)
    if not transcript:
        raise RuntimeError("Got an empty transcript — the recording may have been silent or too short.")
    structured = structure_transcript(transcript)
    return {"transcript": transcript, "structured": structured}
