"""
Dhwani-Code backend
===================

A voice debugging companion: the developer speaks code-mixed Hinglish/Tamil about an
error, and hears back a short, line-specific fix.

    mic audio --> Prisma STT --> Evon reasoning --> Timbre TTS --> audio stream
                      ^               ^
                      |               +-- editor context (code, stack trace)
                      +-- keyterms built from the code, so jargon survives transcription

Sections
    1. Configuration
    2. Errors
    3. Data models
    4. Text helpers (keyterms, prompt assembly, speech clean-up)
    5. Prisma client  (speech-to-text)
    6. Evon client    (reasoning) plus a demo analyzer
    7. Timbre client  (text-to-speech, streamed)
    8. Pipeline       (STT -> Evon -> TTS as one stream of events)
    9. HTTP / WebSocket routes

Run:  python main.py        (or: uvicorn main:app --reload)
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import io
import json
import logging
import os
import re
import time
import uuid
import wave
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

load_dotenv()
log = logging.getLogger("dhwani")
BASE_DIR = Path(__file__).resolve().parent


# ===========================================================================
# 1. Configuration
# ===========================================================================
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_num(name: str, default: Any, cast: Any) -> Any:
    raw = _env(name)
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        log.warning("Ignoring invalid %s=%r", name, raw)
        return default


_BIAS_SCORES = (0.0, 0.5, 1.0, 1.5, 2.0)  # the only values Prisma v2.5 accepts
_TTS_RATES = (8000, 16000, 22050, 24000, 44100, 48000)


@dataclass(frozen=True)
class Profile:
    """How one spoken language is handled at each stage of the pipeline."""

    key: str
    label: str
    stt_language: str  # Prisma v2.5: exactly one language code
    stt_v3_language: str  # Prisma v3.0: comma-separated hints
    tts_language: str  # Timbre `language` value
    tts_voice: str  # Timbre v2.5 voice that prefers this language
    roman_script: bool  # True when the spoken reply is written in Latin letters
    reply_style: str  # instruction for Evon
    example_script: str  # one-line example for Evon
    needs_v3: bool = False  # romanised output (`transliterate`) exists only on Prisma v3.0
    v3_format: str = "codemix"  # Prisma v3.0 output style for this profile


PROFILES: dict[str, Profile] = {
    "hinglish": Profile(
        key="hinglish",
        label="Hinglish",
        stt_language="en-IN",  # Prisma's English accepts Hindi-English mixed audio, Latin output
        stt_v3_language="hi-IN,en-IN",
        tts_language="hi-en",
        tts_voice="Poorvi",
        roman_script=True,
        reply_style=(
            "Speak Hinglish: Hindi words written in Roman script, mixed with English "
            "technical terms, the way Indian developers talk to each other."
        ),
        example_script=(
            "Line forty two me loop condition less than or equal to size hai, usko "
            "less than size karo, warna vector out of bounds ho jayega."
        ),
    ),
    # Tanglish and Benglish: Prisma has no code-mixed language codes for them. v3.0's `transliterate`
    # format writes the whole transcript in Latin letters, which is what a romanised Tamil/Bengali
    # developer expects to see. If v3.0 is not available the client falls back to v2.5 in native script.
    "tanglish": Profile(
        key="tanglish",
        label="Tanglish",
        stt_language="ta-IN",
        stt_v3_language="ta-IN,en-IN",
        tts_language="ta-IN",
        tts_voice="Noopur",
        roman_script=False,
        reply_style=(
            "Speak colloquial Tamil written in Tamil script, with English technical terms and "
            "identifiers left in English (Latin script). The developer may write Tamil in Latin "
            "letters, but your spoken reply must use Tamil script so it is pronounced correctly."
        ),
        example_script=(
            "லைன் நாற்பத்தி இரண்டில் loop condition less than or equal to size என்று "
            "இருக்கு, அதை less than size ஆக மாத்து, இல்லன்னா vector out of bounds ஆகும்."
        ),
        needs_v3=True,
        v3_format="transliterate",
    ),
    "benglish": Profile(
        key="benglish",
        label="Benglish",
        stt_language="bn-IN",
        stt_v3_language="bn-IN,en-IN",
        tts_language="bn-IN",
        tts_voice="Kirra",
        roman_script=False,
        reply_style=(
            "Speak conversational Bengali written in Bengali script, with English technical terms "
            "and identifiers left in English (Latin script). The developer may write Bengali in "
            "Latin letters, but your spoken reply must use Bengali script so it is pronounced correctly."
        ),
        example_script=(
            "লাইন বিয়াল্লিশে loop condition less than or equal to size আছে, সেটা "
            "less than size করো, নাহলে vector out of bounds হয়ে যাবে।"
        ),
        needs_v3=True,
        v3_format="transliterate",
    ),
    "hindi": Profile(
        key="hindi",
        label="Hindi",
        stt_language="hi-IN",
        stt_v3_language="hi-IN,en-IN",
        tts_language="hi-IN",
        tts_voice="Nalini",
        roman_script=False,
        reply_style=(
            "Speak Hindi in Devanagari script. Keep programming terms and identifiers "
            "in English (Latin script)."
        ),
        example_script=(
            "लाइन बयालीस में loop condition less than or equal to size है, उसे "
            "less than size करो, वरना vector out of bounds हो जाएगा।"
        ),
    ),
}


@dataclass(frozen=True)
class Settings:
    gnani_api_key: str
    gnani_base_url: str
    gnani_max_concurrency: int
    stt_model: str  # "" means Prisma v2.5, which is the API default
    stt_bias_score: float
    tts_model: str
    tts_voice: str  # empty means "pick per profile"
    tts_speed: float
    tts_sample_rate: int
    evon_base_url: str
    evon_api_key: str
    evon_auth_header: str  # "Authorization" sends a Bearer token; anything else sends the raw key
    evon_model: str
    evon_enable_thinking: bool
    evon_max_tokens: int  # 0 = automatic (600, or 4096 with thinking on)
    evon_timeout: float
    max_upload_bytes: int
    cors_origins: list[str]
    reasoning_label_env: str  # optional name to show in the UI for the reasoning model
    rate_limit_per_hour: int  # per visitor; 0 disables
    daily_cap: int  # all visitors together; 0 disables
    owner_key: str  # requests carrying this key skip the limits

    @classmethod
    def from_env(cls) -> "Settings":
        stt = _env("STT_MODEL", "prisma-v2.5").lower()
        tts = _env("TTS_MODEL", "timbre-v2.5").lower()
        score = float(_env_num("STT_BIAS_SCORE", 1.0, float))
        rate = int(_env_num("TTS_SAMPLE_RATE", 24000, int))
        return cls(
            gnani_api_key=_env("GNANI_API_KEY"),
            gnani_base_url=_env("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/"),
            gnani_max_concurrency=max(1, int(_env_num("GNANI_MAX_CONCURRENCY", 1, int))),
            # Prisma v2.5 is the default model: the API wants `model` omitted for it.
            stt_model="prisma-v3.0" if stt == "prisma-v3.0" else "",
            stt_bias_score=min(_BIAS_SCORES, key=lambda s: abs(s - score)),
            tts_model="timbre-v3.0" if tts == "timbre-v3.0" else "timbre-v2.5",
            tts_voice=_env("TTS_VOICE"),
            tts_speed=min(1.15, max(0.85, float(_env_num("TTS_SPEED", 1.0, float)))),
            tts_sample_rate=rate if rate in _TTS_RATES else 24000,
            evon_base_url=_env("EVON_BASE_URL").rstrip("/"),
            evon_api_key=_env("EVON_API_KEY"),
            evon_auth_header=_env("EVON_AUTH_HEADER", "Authorization"),
            evon_model=_env("EVON_MODEL", "gnani-evon-v3.3"),
            evon_enable_thinking=_env("EVON_ENABLE_THINKING", "false").lower()
            in ("1", "true", "yes"),
            evon_max_tokens=max(0, int(_env_num("EVON_MAX_TOKENS", 0, int))),
            evon_timeout=float(_env_num("EVON_TIMEOUT_S", 60.0, float)),
            max_upload_bytes=int(_env_num("MAX_UPLOAD_MB", 8, int)) * 1024 * 1024,
            cors_origins=[o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip()],
            reasoning_label_env=_env("REASONING_LABEL"),
            rate_limit_per_hour=max(0, int(_env_num("RATE_LIMIT_PER_HOUR", 8, int))),
            daily_cap=max(0, int(_env_num("DAILY_CAP", 200, int))),
            owner_key=_env("OWNER_KEY"),
        )

    @property
    def stt_label(self) -> str:
        return self.stt_model or "prisma-v2.5"

    @property
    def reasoning_label(self) -> str:
        """Name shown in the UI. It must say what is really answering."""
        if not self.evon_base_url:
            return "Demo rules"
        if self.reasoning_label_env:
            return self.reasoning_label_env
        return "Evon v3.3" if "evon" in self.evon_model.lower() else self.evon_model

    def tts_params(self, profile: Profile) -> tuple[str, str]:
        """(voice, language) for Timbre. Voices are not shared between Timbre versions."""
        if self.tts_model == "timbre-v3.0":
            # v3.0 voices speak every language; it has no code-mixed `hi-en` value.
            language = "hi-IN" if profile.tts_language == "hi-en" else profile.tts_language
            return self.tts_voice or "Shivani", language
        return self.tts_voice or profile.tts_voice, profile.tts_language


# ===========================================================================
# 2. Errors
# ===========================================================================
class PipelineError(Exception):
    """A failure at one stage. Becomes an `error` event on streams and a JSON body elsewhere."""

    def __init__(self, stage: str, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.stage, self.code, self.message, self.status = stage, code, message, status

    def to_event(self) -> dict[str, Any]:
        return {"type": "error", "stage": self.stage, "code": self.code, "message": self.message}


def _parse_error_body(status: int, body: bytes) -> tuple[str, str]:
    """Gnani uses several error shapes; normalise them to (code, message)."""
    fallback = (f"HTTP_{status}", body[:300].decode("utf-8", "replace") or "Empty response")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    detail = data.get("detail")
    if isinstance(detail, dict):  # gateway: auth, credits, rate limit
        return str(detail.get("error_code", f"HTTP_{status}")), str(detail.get("message", ""))
    if isinstance(detail, list):  # FastAPI validation
        msgs = "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', []))}: {d.get('msg')}"
            for d in detail
            if isinstance(d, dict)
        )
        return "VALIDATION_ERROR", msgs
    err = data.get("error")
    if isinstance(err, dict):  # {"success": false, "error": {"type", "message"}}
        return str(err.get("type", f"HTTP_{status}")), str(err.get("message", ""))
    if isinstance(err, str):  # 504 body
        return str(data.get("code", err)), str(data.get("message", err))
    if "message" in data:  # TTS flat errors
        return f"HTTP_{status}", str(data["message"])
    return fallback


_STATUS_HINTS = {
    401: "Check GNANI_API_KEY in your .env file.",
    402: "Your Gnani credits may be used up.",
    403: "The key lacks access to this service, or the credits are gone.",
    429: "This account allows one active request at a time. Wait a moment and retry.",
}


def gnani_error(stage: str, status: int, body: bytes) -> PipelineError:
    code, message = _parse_error_body(status, body)
    hint = _STATUS_HINTS.get(status, "")
    out_status = status if status in (400, 413, 422, 429) else 502
    return PipelineError(stage, code, f"{message} {hint}".strip(), out_status)


# ===========================================================================
# 3. Data models
# ===========================================================================
class EditorContext(BaseModel):
    """What the IDE extension captures when an error appears."""

    file_name: str = Field("untitled", max_length=260)
    language: str = Field("text", max_length=40)
    code: str = Field("", max_length=400_000)
    error_log: str = Field("", max_length=100_000)
    cursor_line: Optional[int] = Field(None, ge=1)


class Fix(BaseModel):
    """Evon's structured answer."""

    root_cause: str = ""
    line_to_fix: Optional[int] = None
    suggested_code: str = ""
    speech_script: str
    confidence: str = "medium"

    @field_validator("line_to_fix", mode="before")
    @classmethod
    def _coerce_line(cls, v: Any) -> Optional[int]:
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            return None
        return n if n >= 1 else None

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, v: Any) -> str:
        v = str(v or "").lower()
        return v if v in ("high", "medium", "low") else "medium"

    @field_validator("speech_script")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("speech_script is empty")
        return v


class AnalyzeRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=2000)
    context: EditorContext
    profile: str = "hinglish"


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=600)
    profile: str = "hinglish"
    format: str = Field("sse", pattern="^(sse|wav)$")


@dataclass
class AudioClip:
    data: bytes
    filename: str
    content_type: str


@dataclass
class Transcript:
    text: str
    model: str
    latency_ms: int


def get_profile(key: str) -> Profile:
    profile = PROFILES.get((key or "hinglish").lower())
    if profile is None:
        raise PipelineError(
            "request", "UNKNOWN_PROFILE", f"Unknown profile {key!r}. Use one of: {', '.join(PROFILES)}.", 400
        )
    return profile


def build_context(**fields: Any) -> EditorContext:
    try:
        return EditorContext(**{k: v for k, v in fields.items() if v is not None})
    except ValidationError as exc:
        raise PipelineError("request", "INVALID_CONTEXT", str(exc.errors()[0]["msg"]), 400) from exc


# ===========================================================================
# 4. Text helpers
# ===========================================================================
# Programming vocabulary that speech models tend to mangle when it is mixed into Hindi/Tamil.
DEFAULT_KEYTERMS = [
    "vector", "null pointer", "segmentation fault", "segfault", "nullptr", "stack trace",
    "out of bounds", "index out of range", "off by one", "FastAPI", "React", "iterator",
    "dangling pointer", "memory leak", "buffer overflow", "heap", "stack overflow",
    "uninitialized", "undefined reference", "linker error", "compile error", "syntax error",
    "traceback", "exception", "dereference", "infinite loop", "malloc", "array", "pointer",
    "loop condition", "unordered_map", "TypeError", "IndexError", "KeyError", "ValueError",
    "AttributeError", "NoneType", "NullPointerException", "async", "await", "callback",
]

# Words from the user's code that are too common to be worth boosting.
_CODE_STOPWORDS = {
    "for", "int", "the", "and", "not", "return", "else", "elif", "while", "void", "char", "bool",
    "true", "false", "self", "def", "class", "import", "from", "none", "const", "auto", "std",
    "include", "static", "struct", "public", "private", "this", "new", "try", "catch", "with",
    "print", "cout", "endl", "main", "size", "long", "double", "float", "string",
}


def split_identifier(name: str) -> list[str]:
    """sumReadings -> [sum, Readings];  total_count -> [total, count]."""
    parts: list[str] = []
    for chunk in name.split("_"):
        parts += re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", chunk)
    return parts


def _top_identifiers(code: str, n: int) -> list[str]:
    return [w for w, _ in Counter(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", code)).most_common(n)]


def build_bias_words(ctx: EditorContext, limit: int = 100) -> list[str]:
    """Word list for Prisma v2.5 `bias_list`.

    v2.5 has no `keyterms`. Its word boosting accepts single words made of letters only;
    one bad entry makes the API silently drop the whole list, so be strict here.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(word: str, from_code: bool = False) -> None:
        low = word.lower()
        if not (word.isascii() and word.isalpha()) or low in seen or not (2 <= len(low) <= 24):
            return
        if from_code and (len(low) < 3 or low in _CODE_STOPWORDS):
            return
        seen.add(low)
        out.append(word)

    for term in DEFAULT_KEYTERMS:
        for word in re.findall(r"[A-Za-z]+", term):
            add(word)
    for ident in _top_identifiers(ctx.code, 80):
        add(ident, from_code=True)
        for part in split_identifier(ident):
            add(part, from_code=True)
    return out[:limit]


def build_keyterm_phrases(ctx: EditorContext, limit: int = 120) -> list[str]:
    """Phrase list for Prisma v3.0 `keyterms` (phrases allowed, up to 200)."""
    phrases = list(dict.fromkeys(DEFAULT_KEYTERMS))
    for ident in _top_identifiers(ctx.code, 60):
        if ident.lower() not in _CODE_STOPWORDS and ident not in phrases:
            phrases.append(ident)
    return phrases[:limit]


def build_topic(ctx: EditorContext) -> str:
    first_error = next((ln.strip() for ln in ctx.error_log.splitlines() if ln.strip()), "")
    topic = (
        f"A developer is debugging {ctx.language} code in {ctx.file_name}, speaking code-mixed "
        f"Indian English with programming terms. Error: {first_error[:240]}"
    )
    return topic[:1500]


MAX_CODE_LINES = 1500
_LOG_LINE_RE = re.compile(r"\.\w+\"?[:,]\s*(?:line\s+)?(\d{1,5})")


def number_code(code: str, focus: Optional[int]) -> str:
    """Prefix every line with its number so Evon can cite exact lines."""
    lines = code.splitlines()
    start = 0
    if len(lines) > MAX_CODE_LINES:
        centre = (focus or 1) - 1
        start = max(0, min(centre - MAX_CODE_LINES // 2, len(lines) - MAX_CODE_LINES))
        lines = lines[start : start + MAX_CODE_LINES]
    width = len(str(start + len(lines)))
    return "\n".join(f"{start + i + 1:>{width}} | {line}" for i, line in enumerate(lines))


EVON_SYSTEM_PROMPT = """You are Dhwani, a senior engineer sitting next to a developer and helping them by voice.

You receive the active file, its language, the error log or stack trace captured from the editor, the code with line numbers, and what the developer just said. The developer's words are a speech transcript. It is often code-mixed, may be written in Latin letters (romanised Hindi, Tamil or Bengali) or in the native script, and may contain recognition mistakes in technical terms.

Answer what the developer actually asked, using their code and error log as context. Decide which kind of question it is:
1. A bug or fix question (why does this crash, what is wrong, how do I fix it): find the most likely root cause using the error log first, then the code. Trust the line numbers in the listing. Point to ONE exact line to change. That is the line that is wrong, which can differ from the line where the program crashes. Give the smallest correct replacement for that line.
2. An explanation question (what is X, what does this line do, explain this error): explain it in plain words, using their own code as the example where it helps. Set line_to_fix to null and suggested_code to an empty string, unless a code change is really needed.
3. A follow-up or opinion question about the code (is this efficient, can I do it differently): answer it directly. Give a line and code only if you are recommending a change.
4. Anything that is not about programming: say so in one sentence and offer what you can help with.
Never repeat a diagnosis the developer did not ask about.

Reply with a single JSON object and nothing else. No markdown fences, no commentary:
{{
  "root_cause": "<one sentence in English: the cause for a bug question, or the short answer for an explanation>",
  "line_to_fix": <integer line number from the listing, or null when the answer is not about one specific line>,
  "suggested_code": "<the corrected line exactly as it should appear in the file, or an empty string>",
  "speech_script": "<what will be spoken>",
  "confidence": "high" | "medium" | "low"
}}

Rules for speech_script (it is turned into audio):
- {reply_style}
- For a fix: at most two short sentences and under 35 words. Start with the line number and what to change, then give the reason.
- For an explanation or follow-up: at most three short sentences and under 60 words. Answer the question first.
- Talk like a colleague at the next desk. No greetings, no "as an AI".
- No code blocks, backticks or symbols. Say operators in words ("less than or equal to") and write numbers as words ("line forty two").
- Keep identifiers and programming terms (vector, nullptr, segmentation fault) in English.
- If the question is unclear, or the code and log do not show the cause, set confidence to "low", line_to_fix to null, and use the script to ask one specific question or name the one thing to check.

Example of a good speech_script for a fix question: {example}"""


def build_messages(transcript: str, ctx: EditorContext, profile: Profile) -> list[dict[str, str]]:
    focus = ctx.cursor_line
    if focus is None:
        m = _LOG_LINE_RE.search(ctx.error_log)
        focus = int(m.group(1)) if m else None
    cursor = f"\nCursor line: {ctx.cursor_line}" if ctx.cursor_line else ""
    user = (
        f"File: {ctx.file_name} (language: {ctx.language}){cursor}\n\n"
        f"=== ERROR LOG / STACK TRACE ===\n{ctx.error_log[:6000].strip() or '(none captured)'}\n\n"
        f"=== CODE (line number | text) ===\n{number_code(ctx.code, focus)}\n\n"
        f"=== DEVELOPER SAID (speech transcript) ===\n{transcript.strip()}"
    )
    system = EVON_SYSTEM_PROMPT.format(reply_style=profile.reply_style, example=profile.example_script)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating think blocks and code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "</think>" in text:  # the opening tag is often stripped by the server's reasoning parser
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


# --- speech clean-up -------------------------------------------------------
_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_OPERATORS = [
    ("<=", " less than or equal to "), (">=", " greater than or equal to "),
    ("==", " equals "), ("!=", " not equal to "), ("->", " arrow "), ("&&", " and "),
    ("||", " or "), ("+=", " plus equals "), ("++", " plus plus "), ("--", " minus minus "),
    ("<", " less than "), (">", " greater than "),
]


def int_to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {int_to_words(rest)}" if rest else "")
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        return f"{_ONES[thousands]} thousand" + (f" {int_to_words(rest)}" if rest else "")
    return " ".join(_ONES[int(d)] for d in str(n))


def prepare_for_tts(text: str, roman_script: bool) -> str:
    """Make the script safe to read aloud: symbols and digits are mispronounced by TTS."""
    for symbol, words in _OPERATORS:
        text = text.replace(symbol, words)
    text = re.sub(r"[`*#]+", "", text).replace("_", " ")
    if roman_script:
        text = re.sub(r"\b\d+\b", lambda m: int_to_words(int(m.group())), text)
    return re.sub(r"\s+", " ", text).strip()[:480]


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# --- audio validation ------------------------------------------------------
SUPPORTED_EXT = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}
_CT_TO_EXT = {
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3", "audio/ogg": ".ogg", "audio/flac": ".flac", "audio/aac": ".aac",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
}
MAX_AUDIO_SECONDS = 30.0  # Prisma REST limit


def validate_audio(data: bytes, filename: str, content_type: str, max_bytes: int) -> AudioClip:
    if not data:
        raise PipelineError("audio", "EMPTY_AUDIO", "The audio upload is empty.", 400)
    if len(data) > max_bytes:
        raise PipelineError("audio", "AUDIO_TOO_LARGE", f"Audio is larger than {max_bytes // 1048576} MB.", 413)
    if data[:4] == b"\x1a\x45\xdf\xa3":
        raise PipelineError(
            "audio", "UNSUPPORTED_AUDIO_FORMAT",
            "WebM audio is not accepted by Prisma. Send WAV, MP3, OGG, FLAC, AAC or M4A. "
            "The bundled web UI converts recordings to WAV for you.", 415,
        )
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_EXT:
        ext = _CT_TO_EXT.get((content_type or "").split(";")[0].strip().lower(), "")
    if not ext:
        raise PipelineError(
            "audio", "UNSUPPORTED_AUDIO_FORMAT",
            f"Unsupported audio type. Use one of: {', '.join(sorted(SUPPORTED_EXT))}.", 415,
        )
    if ext == ".wav":
        try:
            with wave.open(io.BytesIO(data)) as w:
                seconds = w.getnframes() / float(w.getframerate())
            if seconds > MAX_AUDIO_SECONDS:
                raise PipelineError(
                    "audio", "MAX_AUDIO_DURATION_EXCEEDED",
                    f"Audio is {seconds:.0f} s long; Prisma's REST limit is 30 s.", 413,
                )
        except (wave.Error, EOFError, ZeroDivisionError):
            pass  # not a plain PCM WAV; let Prisma decide
    return AudioClip(data=data, filename=f"speech{ext}", content_type=_ext_to_ct(ext))


def _ext_to_ct(ext: str) -> str:
    return next((ct for ct, e in _CT_TO_EXT.items() if e == ext), "application/octet-stream")


# ===========================================================================
# 5. Prisma client (speech-to-text)
# ===========================================================================
# Errors that mean "Prisma v3.0 will not take this request", as opposed to bad audio or rate limits.
_V3_REFUSALS = {
    "INVALID_MODEL", "MODEL_UNSUPPORTED_LANGUAGE", "INVALID_FORMAT", "INVALID_CONTEXT",
    "INVALID_LANGUAGE_CODE", "SCOPE_NOT_ALLOWED", "FORBIDDEN", "VALIDATION_ERROR",
}


class PrismaClient:
    """POST /stt/v3 (multipart). Handles both Prisma generations.

    Hinglish and Hindi run on the model chosen by STT_MODEL (v2.5 by default). Tanglish and Benglish
    need v3.0's `transliterate` output (romanised text), so they use v3.0 whatever STT_MODEL says and
    fall back to v2.5 in native script if v3.0 refuses the request.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient, gate: asyncio.Semaphore):
        self.s, self._http, self._gate = settings, http, gate

    def _form(self, ctx: EditorContext, profile: Profile, use_v3: bool) -> dict[str, str]:
        if use_v3:
            # Real keyterms and a topic. `transliterate` writes everything in Latin letters;
            # `codemix` keeps English words in Latin script and the rest in the native script.
            return {
                "model": "prisma-v3.0",
                "language_code": profile.stt_v3_language,
                "format": profile.v3_format if profile.needs_v3 else "codemix",
                "keyterms": json.dumps(build_keyterm_phrases(ctx), ensure_ascii=False),
                "topic": build_topic(ctx),
            }
        form = {"language_code": profile.stt_language, "format": "verbatim"}
        words = build_bias_words(ctx)
        if words and self.s.stt_bias_score > 0:
            form["bias_list"] = json.dumps(words)
            form["bias_score"] = str(self.s.stt_bias_score)
        return form

    @staticmethod
    def _text_of(body: dict[str, Any]) -> str:
        t = body.get("transcript")
        if isinstance(t, str):
            return t.strip()
        if isinstance(t, dict):
            return str(t.get("text") or t.get("transcript") or "").strip()
        out = body.get("output")
        return str(out.get("literal", "")).strip() if isinstance(out, dict) else ""

    async def transcribe(self, clip: AudioClip, ctx: EditorContext, profile: Profile) -> Transcript:
        if not self.s.gnani_api_key:
            raise PipelineError("stt", "NO_API_KEY", "GNANI_API_KEY is not set. Add it to .env and restart.", 503)
        global_v3 = self.s.stt_model == "prisma-v3.0"
        use_v3 = global_v3 or profile.needs_v3
        try:
            return await self._call(clip, ctx, profile, use_v3)
        except PipelineError as exc:
            if use_v3 and not global_v3 and exc.code in _V3_REFUSALS:
                log.warning("Prisma v3.0 refused the %s request (%s); retrying on v2.5", profile.key, exc.code)
                return await self._call(clip, ctx, profile, False)
            raise

    async def _call(self, clip: AudioClip, ctx: EditorContext, profile: Profile, use_v3: bool) -> Transcript:
        headers = {"X-API-Key-ID": self.s.gnani_api_key, "X-API-Request-ID": uuid.uuid4().hex}
        started = time.perf_counter()
        resp: Optional[httpx.Response] = None
        for attempt in range(3):
            try:
                async with self._gate:
                    resp = await self._http.post(
                        f"{self.s.gnani_base_url}/stt/v3",
                        headers=headers,
                        data=self._form(ctx, profile, use_v3),
                        files={"audio_file": (clip.filename, clip.data, clip.content_type)},
                        timeout=httpx.Timeout(60.0, connect=10.0),
                    )
            except httpx.HTTPError as exc:
                raise PipelineError("stt", "PRISMA_UNREACHABLE", f"Could not reach Prisma: {exc.__class__.__name__}.", 502) from exc
            if resp.status_code != 429:
                break
            await asyncio.sleep(0.8 * (attempt + 1))
        assert resp is not None
        if resp.status_code != 200:
            raise gnani_error("stt", resp.status_code, resp.content)
        try:
            body = resp.json()
        except ValueError as exc:
            raise PipelineError("stt", "BAD_STT_RESPONSE", "Prisma returned a non-JSON response.", 502) from exc
        return Transcript(
            text=self._text_of(body),
            model=str(body.get("model") or ("prisma-v3.0" if use_v3 else self.s.stt_label)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# ===========================================================================
# 6. Evon client (reasoning) and demo analyzer
# ===========================================================================
class EvonClient:
    """Evon v3.3 is open weights: talk to your own vLLM / SGLang server (OpenAI-compatible)."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s, self._http = settings, http
        self._use_extras = True  # vLLM/SGLang-only request fields; switched off if the server rejects them

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.s.evon_api_key:
            if self.s.evon_auth_header.lower() == "authorization":
                headers["Authorization"] = f"Bearer {self.s.evon_api_key}"
            else:  # e.g. X-API-Key-ID, the header Gnani uses for its speech APIs
                headers[self.s.evon_auth_header] = self.s.evon_api_key
        try:
            return await self._http.post(
                f"{self.s.evon_base_url}/chat/completions",
                headers=headers, json=payload, timeout=self.s.evon_timeout,
            )
        except httpx.HTTPError as exc:
            raise PipelineError(
                "analysis", "EVON_UNREACHABLE",
                f"Could not reach the Evon server at {self.s.evon_base_url} ({exc.__class__.__name__}). "
                "Is vLLM or SGLang running?", 502,
            ) from exc

    async def analyze(self, transcript: str, ctx: EditorContext, profile: Profile) -> Fix:
        thinking = self.s.evon_enable_thinking
        base = {
            "model": self.s.evon_model,
            "messages": build_messages(transcript, ctx, profile),
            "temperature": 0.6,  # the model card's recommended sampling
            "top_p": 0.95,
            "max_tokens": self.s.evon_max_tokens or (4096 if thinking else 600),
            "stream": False,
        }
        # vLLM / SGLang extras. Other OpenAI-compatible servers may reject them, so retry without.
        extras = {
            "chat_template_kwargs": {"enable_thinking": thinking},
            "repetition_penalty": 1.0 if thinking else 1.1,
        }
        if self._use_extras:
            resp = await self._post({**base, **extras})
            if resp.status_code in (400, 422):
                # Probably a server that does not know the vLLM-only fields (most hosted APIs). Retry
                # without them, and keep sending plain requests so later questions do not pay for this.
                self._use_extras = False
                resp = await self._post(base)
                if resp.status_code != 200:
                    self._use_extras = True  # the problem was something else, so do not remember it
                else:
                    log.info("Reasoning endpoint rejected vLLM-only parameters; sending plain requests from now on")
        else:
            resp = await self._post(base)
        if resp.status_code != 200:
            raise PipelineError("analysis", f"EVON_HTTP_{resp.status_code}", f"Evon server error: {resp.text[:300]}", 502)
        try:
            content = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError("analysis", "BAD_EVON_RESPONSE", "Unexpected response shape from the Evon server.", 502) from exc

        try:
            return Fix.model_validate(extract_json(content))
        except (ValueError, ValidationError):
            log.warning("Evon reply was not valid JSON; degrading: %.200s", content)
            plain = re.sub(r"<think>.*?</think>|```\w*", "", content, flags=re.S).strip()
            if not plain:
                raise PipelineError("analysis", "EMPTY_EVON_REPLY", "Evon returned an empty reply.", 502)
            return Fix(root_cause=plain[:300], speech_script=plain[:300], confidence="low")


_DEMO_SCRIPTS = {
    "loop": {
        "hinglish": "Line {n} me loop condition less than or equal to {a} hai. Usko less than {a} karo, warna out of bounds ho jayega.",
        "hindi": "लाइन {n} में loop condition less than or equal to {a} है। उसे less than {a} करो, वरना out of bounds हो जाएगा।",
        "tanglish": "லைன் {n}-ல loop condition less than or equal to {a} என்று இருக்கு. அதை less than {a} ஆக மாத்து, இல்லன்னா out of bounds ஆகும்.",
        "benglish": "লাইন {n}-এ loop condition less than or equal to {a} আছে। সেটা less than {a} করো, নাহলে out of bounds হয়ে যাবে।",
    },
    "range": {
        "hinglish": "Line {n} me range len {a} plus one hai. Plus one hata do, warna index out of range error aayega.",
        "hindi": "लाइन {n} में range len {a} plus one है। plus one हटा दो, वरना index out of range error आएगा।",
        "tanglish": "லைன் {n}-ல range len {a} plus one இருக்கு. plus one-ஐ எடுத்துடு, இல்லன்னா index out of range error வரும்.",
        "benglish": "লাইন {n}-এ range len {a} plus one আছে। plus one সরিয়ে দাও, নাহলে index out of range error আসবে।",
    },
    "null": {
        "hinglish": "Line {n} pe {a} null ho sakta hai aur tum usko dereference kar rahe ho. Pehle {a} not equal to nullptr check lagao, warna segmentation fault aayega.",
        "hindi": "लाइन {n} पर {a} null हो सकता है और तुम उसे dereference कर रहे हो। पहले {a} not equal to nullptr check लगाओ, वरना segmentation fault आएगा।",
        "tanglish": "லைன் {n}-ல {a} null ஆக இருக்கலாம், அதை dereference பண்றீங்க. முதல்ல {a} not equal to nullptr check போடுங்க, இல்லன்னா segmentation fault வரும்.",
        "benglish": "লাইন {n}-এ {a} null হতে পারে আর তুমি সেটা dereference করছো। আগে {a} not equal to nullptr check দাও, নাহলে segmentation fault হবে।",
    },
}


def _demo_say(rule: str, profile: Profile, **fields: Any) -> str:
    table = _DEMO_SCRIPTS[rule]
    return table.get(profile.key, table["hinglish"]).format(**fields)


class DemoAnalyzer:
    """Rule-based stand-in so the voice loop works before a reasoning model is connected.

    It only recognises three classic patterns and is NOT an AI model; the UI says so.
    Used when EVON_BASE_URL is empty.
    """

    async def analyze(self, transcript: str, ctx: EditorContext, profile: Profile) -> Fix:
        for n, line in enumerate(ctx.code.splitlines(), start=1):
            # C-family off-by-one: for (...; i <= bound; ...)
            m = re.search(r"for\s*\(.*?;\s*\w+\s*<=\s*([\w.()\->]+?)\s*;", line)
            if m:
                bound = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]", " ", m.group(1))).strip()
                return Fix(
                    root_cause="The loop uses <= with the container size, so the last iteration reads one element past the end.",
                    line_to_fix=n, suggested_code=line.strip().replace("<=", "<", 1), confidence="high",
                    speech_script=_demo_say("loop", profile, n=n, a=bound),
                )
            # Python off-by-one: range(len(x) + 1)
            m = re.search(r"range\(\s*len\((\w+)\)\s*\+\s*1\s*\)", line)
            if m:
                return Fix(
                    root_cause="range(len(x) + 1) yields one index beyond the last element, so indexing raises IndexError.",
                    line_to_fix=n, suggested_code=line.strip().replace(f"len({m.group(1)}) + 1", f"len({m.group(1)})"),
                    confidence="high",
                    speech_script=_demo_say("range", profile, n=n, a=m.group(1)),
                )
            # Null dereference in a loop condition: while (p->field ...)
            m = re.search(r"while\s*\(\s*(\w+)\s*->", line)
            if m and "nullptr" not in line and "NULL" not in line:
                cond = re.search(r"while\s*\((.*)\)", line).group(1)  # type: ignore[union-attr]
                return Fix(
                    root_cause=f"{m.group(1)} can become null when the loop walks off the end of the list, and it is dereferenced without a check.",
                    line_to_fix=n, suggested_code=line.strip().replace(cond, f"{m.group(1)} != nullptr && {cond}", 1),
                    confidence="high",
                    speech_script=_demo_say("null", profile, n=n, a=m.group(1)),
                )
        raise PipelineError(
            "analysis", "EVON_NOT_CONFIGURED",
            "No reasoning model is connected, and the demo rules only know three bug patterns. "
            "Set EVON_BASE_URL in .env (see the README) to debug arbitrary code.", 503,
        )


# ===========================================================================
# 7. Timbre client (text-to-speech)
# ===========================================================================
class TimbreClient:
    """POST /api/v1/tts/sse: Server-Sent Events carrying base64 audio chunks."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient, gate: asyncio.Semaphore):
        self.s, self._http, self._gate = settings, http, gate

    async def stream_pcm(self, text: str, profile: Profile) -> AsyncIterator[bytes]:
        """Yield raw 16-bit mono PCM (at settings.tts_sample_rate) as Timbre produces it."""
        if not self.s.gnani_api_key:
            raise PipelineError("tts", "NO_API_KEY", "GNANI_API_KEY is not set. Add it to .env and restart.", 503)
        voice, language = self.s.tts_params(profile)
        payload = {
            "text": text, "voice": voice, "model": self.s.tts_model, "language": language,
            "speed": self.s.tts_speed,
            # Raw PCM chunks can be concatenated and scheduled straight into Web Audio.
            "audio_config": {
                "sample_rate": self.s.tts_sample_rate, "num_channels": 1, "sample_width": 2,
                "encoding": "linear_pcm", "container": "raw",
            },
        }
        headers = {
            "X-API-Key-ID": self.s.gnani_api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            async with self._gate:
                async with self._http.stream(
                    "POST", f"{self.s.gnani_base_url}/api/v1/tts/sse",
                    headers=headers, json=payload, timeout=httpx.Timeout(60.0, connect=10.0),
                ) as resp:
                    if resp.status_code != 200:
                        raise gnani_error("tts", resp.status_code, await resp.aread())
                    event = ""
                    async for line in resp.aiter_lines():
                        if not line:
                            event = ""
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                            except ValueError:
                                continue
                            if event == "error" or data.get("type") == "error":
                                raise PipelineError("tts", "TTS_STREAM_ERROR", str(data.get("message") or "Timbre reported an error."))
                            if event == "chunk" and data.get("audio"):
                                yield base64.b64decode(data["audio"])
                            elif event == "complete":
                                return
        except httpx.HTTPError as exc:
            raise PipelineError("tts", "TIMBRE_UNREACHABLE", f"Could not reach Timbre: {exc.__class__.__name__}.", 502) from exc


# ===========================================================================
# 8. Pipeline
# ===========================================================================
class RateLimiter:
    """In-memory limits so a shared link cannot drain the Gnani credits. 0 disables a limit.

    State lives in this process, so it resets when the server restarts or sleeps.
    """

    def __init__(self, per_hour: int, daily_cap: int):
        self.per_hour, self.daily_cap = per_hour, daily_cap
        self._hits: dict[str, deque[float]] = {}
        self._day = ""
        self._day_count = 0

    def check(self, ip: str, now: Optional[float] = None) -> Optional[str]:
        """Count one request. Returns None if allowed, otherwise the message to show the visitor."""
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.gmtime(now + 19800))  # day boundaries in India time
        if day != self._day:
            self._day, self._day_count = day, 0
        if self.daily_cap and self._day_count >= self.daily_cap:
            return "Today's free demo limit has been reached. Please try again tomorrow."
        if self.per_hour:
            hits = self._hits.setdefault(ip, deque())
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= self.per_hour:
                wait = int((3600 - (now - hits[0])) // 60) + 1
                return f"You have used your {self.per_hour} tries for this hour. Please try again in about {wait} min."
            hits.append(now)
            if len(self._hits) > 5000:  # keep memory bounded
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 3600]:
                    del self._hits[key]
        self._day_count += 1
        return None


def client_ip(conn: Any) -> str:
    """Visitor address. Hosts such as Render put the real one first in X-Forwarded-For."""
    forwarded = conn.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return conn.client.host if conn.client else "unknown"


class Services:
    """Everything the routes need, created once at startup."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        gate = asyncio.Semaphore(settings.gnani_max_concurrency)
        self.settings = settings
        self.http = http
        self.stt = PrismaClient(settings, http, gate)
        self.tts = TimbreClient(settings, http, gate)
        self.evon = EvonClient(settings, http) if settings.evon_base_url else None
        self.demo = DemoAnalyzer()
        self.limiter = RateLimiter(settings.rate_limit_per_hour, settings.daily_cap)

    @property
    def evon_mode(self) -> str:
        return "evon" if self.evon else "demo"

    async def analyze(self, transcript: str, ctx: EditorContext, profile: Profile) -> Fix:
        return await (self.evon or self.demo).analyze(transcript, ctx, profile)


def enforce_limits(svc: Services, conn: Any) -> None:
    """Raise a 429 if this visitor (or the whole demo today) is over its limit."""
    owner, sent = svc.settings.owner_key, conn.headers.get("x-owner-key", "")
    if owner and sent and hmac.compare_digest(sent.encode(), owner.encode()):
        return
    message = svc.limiter.check(client_ip(conn))
    if message:
        raise PipelineError("request", "RATE_LIMITED", message, 429)


def _ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


async def run_pipeline(
    svc: Services,
    *,
    ctx: EditorContext,
    profile: Profile,
    clip: Optional[AudioClip] = None,
    typed: Optional[str] = None,
    speak: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Speech (or typed text) in, a stream of events out.

    Events: status, transcript, fix, audio_start, audio (holds `pcm` bytes), done, error.
    The SSE route base64-encodes `pcm`; the WebSocket route sends it as a binary frame.
    """
    request_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    timings: dict[str, int] = {}
    stage = "stt"
    try:
        if typed and typed.strip():
            transcript = typed.strip()
            timings["stt_ms"] = 0
            yield {"type": "transcript", "text": transcript, "model": "typed", "latency_ms": 0}
        else:
            if clip is None:
                raise PipelineError("stt", "NO_INPUT", "Send audio or a typed question.", 400)
            yield {"type": "status", "stage": "stt"}
            result = await svc.stt.transcribe(clip, ctx, profile)
            timings["stt_ms"] = result.latency_ms
            if not result.text:
                raise PipelineError("stt", "EMPTY_TRANSCRIPT", "No speech was detected. Move closer to the mic and try again.", 422)
            transcript = result.text
            yield {"type": "transcript", "text": transcript, "model": result.model, "latency_ms": result.latency_ms}

        stage = "analysis"
        yield {"type": "status", "stage": "analysis"}
        t = time.perf_counter()
        fix = await svc.analyze(transcript, ctx, profile)
        timings["evon_ms"] = _ms(t)
        script = prepare_for_tts(fix.speech_script, profile.roman_script)
        yield {"type": "fix", "fix": fix.model_dump(), "spoken": script, "provider": svc.evon_mode, "label": svc.settings.reasoning_label, "latency_ms": timings["evon_ms"]}

        if speak:
            stage = "tts"
            yield {"type": "status", "stage": "tts"}
            t = time.perf_counter()
            yield {
                "type": "audio_start", "sample_rate": svc.settings.tts_sample_rate,
                "channels": 1, "encoding": "pcm_s16le", "text": script,
            }
            seq = 0
            async for pcm in svc.tts.stream_pcm(script, profile):
                if seq == 0:
                    timings["tts_first_audio_ms"] = _ms(t)
                seq += 1
                yield {"type": "audio", "seq": seq, "pcm": pcm}
            if seq == 0:
                raise PipelineError("tts", "NO_AUDIO", "Timbre returned no audio.", 502)

        timings["total_ms"] = _ms(t0)
        log.info("pipeline %s ok %s", request_id, timings)
        yield {"type": "done", "request_id": request_id, "timings": timings}
    except PipelineError as exc:
        log.warning("pipeline %s failed at %s: %s %s", request_id, exc.stage, exc.code, exc.message)
        yield exc.to_event()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - last-resort guard so a stream always ends cleanly
        log.exception("pipeline %s crashed during %s", request_id, stage)
        yield PipelineError(stage, "INTERNAL_ERROR", "Something went wrong on the server. Check its logs.", 500).to_event()


def sse_pack(event: dict[str, Any]) -> bytes:
    payload = dict(event)
    if "pcm" in payload:
        payload["data"] = base64.b64encode(payload.pop("pcm")).decode("ascii")
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def _sse_stream(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    async for event in events:
        yield sse_pack(event)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ===========================================================================
# 9. Routes
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20)) as http:
        app.state.svc = Services(settings, http)
        if not settings.gnani_api_key:
            log.warning("GNANI_API_KEY is not set: speech endpoints will return 503 until you add it to .env")
        if not settings.evon_base_url:
            log.warning("EVON_BASE_URL is not set: using the demo analyzer (3 known bug patterns)")
        yield


app = FastAPI(title="Dhwani-Code", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=Settings.from_env().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PipelineError)
async def _pipeline_error_handler(_: Request, exc: PipelineError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"stage": exc.stage, "code": exc.code, "message": exc.message}},
    )


def _svc(request_or_ws: Any) -> Services:
    return request_or_ws.app.state.svc  # type: ignore[no-any-return]


@app.get("/", include_in_schema=False)
async def index() -> Response:
    page = BASE_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "index.html not found next to main.py"}, status_code=404)
    return FileResponse(page, media_type="text/html")


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    s = _svc(request).settings
    return {
        "status": "ok",
        "gnani_key_configured": bool(s.gnani_api_key),
        "stt_model": s.stt_label,
        "tts_model": s.tts_model,
        "reasoning": _svc(request).evon_mode,
        "reasoning_label": s.reasoning_label,
    }


@app.get("/api/config")
async def config(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    s = svc.settings
    return {
        "profiles": [{"key": p.key, "label": p.label} for p in PROFILES.values()],
        "stt_model": s.stt_label,
        "tts_model": s.tts_model,
        "reasoning": svc.evon_mode,
        "reasoning_label": s.reasoning_label,
        "evon_model": s.evon_model if svc.evon else None,
        "limits": {"per_hour": s.rate_limit_per_hour, "daily_cap": s.daily_cap},
        "gnani_key_configured": bool(s.gnani_api_key),
        "max_audio_seconds": MAX_AUDIO_SECONDS,
        "default_keyterms": DEFAULT_KEYTERMS,
    }


# --- individual stages: handy for testing each Gnani model on its own -------
@app.post("/api/transcribe")
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    profile: str = Form("hinglish"),
    file_name: str = Form("untitled"),
    language: str = Form("text"),
    code: str = Form(""),
    error_log: str = Form(""),
) -> dict[str, Any]:
    """Audio in, transcript out (Prisma). Send `code` to bias recognition towards your identifiers."""
    svc = _svc(request)
    enforce_limits(svc, request)
    clip = validate_audio(await audio.read(), audio.filename or "", audio.content_type or "", svc.settings.max_upload_bytes)
    ctx = build_context(file_name=file_name, language=language, code=code, error_log=error_log)
    result = await svc.stt.transcribe(clip, ctx, get_profile(profile))
    return {"transcript": result.text, "model": result.model, "latency_ms": result.latency_ms}


@app.post("/api/analyze")
async def analyze(request: Request, body: AnalyzeRequest) -> dict[str, Any]:
    """Transcript + editor context in, structured fix out (Evon)."""
    svc = _svc(request)
    enforce_limits(svc, request)
    profile = get_profile(body.profile)
    t = time.perf_counter()
    fix = await svc.analyze(body.transcript, body.context, profile)
    return {
        "fix": fix.model_dump(),
        "spoken": prepare_for_tts(fix.speech_script, profile.roman_script),
        "provider": svc.evon_mode,
        "label": svc.settings.reasoning_label,
        "latency_ms": _ms(t),
    }


@app.post("/api/speak", response_model=None)
async def speak(request: Request, body: SpeakRequest) -> Response:
    """Text in, speech out (Timbre). `format: "sse"` streams PCM events; `"wav"` returns one WAV file."""
    svc = _svc(request)
    enforce_limits(svc, request)
    profile = get_profile(body.profile)
    text = prepare_for_tts(body.text, profile.roman_script)
    rate = svc.settings.tts_sample_rate

    if body.format == "wav":
        pcm = b"".join([chunk async for chunk in svc.tts.stream_pcm(text, profile)])
        if not pcm:
            raise PipelineError("tts", "NO_AUDIO", "Timbre returned no audio.", 502)
        return Response(pcm_to_wav(pcm, rate), media_type="audio/wav")

    async def events() -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"type": "audio_start", "sample_rate": rate, "channels": 1, "encoding": "pcm_s16le", "text": text}
            seq = 0
            async for pcm in svc.tts.stream_pcm(text, profile):
                seq += 1
                yield {"type": "audio", "seq": seq, "pcm": pcm}
            yield {"type": "done"}
        except PipelineError as exc:
            yield exc.to_event()

    return StreamingResponse(_sse_stream(events()), media_type="text/event-stream", headers=SSE_HEADERS)


# --- the whole loop in one call ---------------------------------------------
@app.post("/api/debug", response_model=None)
async def debug(
    request: Request,
    audio: Optional[UploadFile] = File(None),
    transcript: Optional[str] = Form(None),
    profile: str = Form("hinglish"),
    file_name: str = Form("untitled"),
    language: str = Form("text"),
    code: str = Form(""),
    error_log: str = Form(""),
    cursor_line: Optional[int] = Form(None),
    speak_reply: bool = Form(True),
) -> StreamingResponse:
    """Speech + editor context in; an SSE stream out: transcript, fix, then audio chunks.

    Send `transcript` instead of `audio` to skip speech recognition (typed questions, testing).
    """
    svc = _svc(request)
    enforce_limits(svc, request)
    prof = get_profile(profile)
    ctx = build_context(file_name=file_name, language=language, code=code, error_log=error_log, cursor_line=cursor_line)
    clip = None
    if audio is not None:
        clip = validate_audio(await audio.read(), audio.filename or "", audio.content_type or "", svc.settings.max_upload_bytes)
    elif not (transcript and transcript.strip()):
        raise PipelineError("request", "NO_INPUT", "Send an `audio` file or a `transcript`.", 400)
    events = run_pipeline(svc, ctx=ctx, profile=prof, clip=clip, typed=transcript, speak=speak_reply)
    return StreamingResponse(_sse_stream(events), media_type="text/event-stream", headers=SSE_HEADERS)


@app.websocket("/ws/debug")
async def ws_debug(ws: WebSocket) -> None:
    """Long-lived session for an IDE extension.

    client -> {"type":"context", "file_name", "language", "code", "error_log", "cursor_line", "profile"}
              (send whenever the editor state changes)
    client -> binary frames: one audio clip (WAV/MP3/OGG/FLAC/AAC/M4A bytes), then {"type":"end_audio"}
    client -> {"type":"ask", "text": "..."}   (typed question, skips speech recognition)
    client -> {"type":"reset"}                (drop buffered audio)
    server -> the same events as /api/debug as JSON text frames, except audio chunks,
              which arrive as binary frames of raw 16-bit mono PCM after `audio_start`.
    """
    await ws.accept()
    svc = _svc(ws)
    ctx = EditorContext()
    profile_key = "hinglish"
    buffer = bytearray()
    audio_name, audio_type = "speech.wav", "audio/wav"

    async def send(event: dict[str, Any]) -> None:
        if "pcm" in event:
            await ws.send_bytes(event["pcm"])
        else:
            await ws.send_text(json.dumps(event, ensure_ascii=False))

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                buffer += msg["bytes"]
                if len(buffer) > svc.settings.max_upload_bytes:
                    buffer.clear()
                    await send(PipelineError("audio", "AUDIO_TOO_LARGE", "Audio exceeds the upload limit.", 413).to_event())
                continue
            try:
                data = json.loads(msg.get("text") or "")
                kind = data.get("type")
            except (ValueError, AttributeError):
                await send(PipelineError("request", "BAD_MESSAGE", "Send JSON text frames or binary audio.", 400).to_event())
                continue

            try:
                if kind == "context":
                    fields = {k: data.get(k) for k in EditorContext.model_fields}
                    ctx = build_context(**fields)
                    profile_key = str(data.get("profile") or profile_key)
                    audio_name = str(data.get("audio_name") or audio_name)
                    audio_type = str(data.get("audio_type") or audio_type)
                elif kind == "reset":
                    buffer.clear()
                elif kind in ("end_audio", "ask"):
                    enforce_limits(svc, ws)
                    prof = get_profile(profile_key)
                    clip = typed = None
                    if kind == "ask":
                        typed = str(data.get("text") or "")
                    else:
                        clip = validate_audio(bytes(buffer), audio_name, audio_type, svc.settings.max_upload_bytes)
                    buffer.clear()
                    async for event in run_pipeline(svc, ctx=ctx, profile=prof, clip=clip, typed=typed):
                        await send(event)
                else:
                    await send(PipelineError("request", "UNKNOWN_TYPE", f"Unknown message type {kind!r}.", 400).to_event())
            except PipelineError as exc:
                buffer.clear()
                await send(exc.to_event())
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=_env("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "main:app",
        host=_env("HOST", "127.0.0.1"),
        port=int(_env_num("PORT", 8000, int)),
        reload=_env("RELOAD", "false").lower() in ("1", "true", "yes"),
    )
