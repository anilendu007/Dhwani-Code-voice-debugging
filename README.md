# Dhwani-Code (ध्वनि)

**Debug in the language you think in.**

Dhwani-Code is a voice debugging companion for Indian developers. Ask about code in Hinglish, Tanglish, Benglish, or Hindi; Gemini transcribes the question, checks the code and error context, and returns a suggested fix with a spoken explanation.

- Live demo: <https://dhwani-code-voice-debugging.onrender.com>
- Demo video: <https://youtu.be/i5sSHQD05Xw>
- Built by Riya Tiwari ([@anilendu007](https://github.com/anilendu007)).
- Licence: MIT.

## What it does

1. Shows sample code and errors, or lets you paste your own code and error output.
2. Accepts a spoken or typed question.
3. Sends audio, code, error output, and the question to Gemini through the backend.
4. Shows the diagnosis and suggested source line, and can speak the answer.

Gemini is used for speech-to-text, code analysis, and text-to-speech. The Gemini API key stays on the server.

## Logical bug analysis

The analysis prompt asks Gemini to trace code paths and concrete conditions, inspect data flow, loop bounds, state changes, return values, and edge cases, and compare the code's behavior with the intended behavior. It treats logs as clues rather than proof, and asks for evidence-based fixes rather than matching only familiar error patterns.

The model returns structured JSON with a root cause, optional line number, suggested replacement, spoken answer, and confidence. The backend rejects invalid responses and removes any suggested line number that is outside the supplied file. Model-generated fixes are still suggestions: review and test them before applying.

## Models and architecture

| Stage | Implementation |
|---|---|
| Speech-to-text | Gemini audio understanding |
| Code analysis | Gemini text generation with structured JSON output and higher thinking level |
| Text-to-speech | Gemini TTS model |
| Backend | Python, FastAPI, httpx |
| Frontend | `index.html`, plain JavaScript, Web Audio |
| Hosting | Render or another Python ASGI host |

The browser records audio and converts it to 16 kHz mono WAV. The backend sends it to Gemini, then sends the transcript and editor context for analysis. Gemini TTS returns WAV audio; the backend converts it to PCM for the existing SSE/WebSocket audio player. TTS is generated before the audio event is sent, so playback starts after that generation call completes.

## Local setup

Requirements: Python 3.10+ and a recent browser with MediaRecorder and Web Audio support.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey) and add it to `.env`:

```dotenv
GEMINI_API_KEY=your-gemini-api-key
```

Start the app:

```powershell
python main.py
```

Open <http://127.0.0.1:8000>. The app must be served by FastAPI; opening `index.html` directly or through a static server will not work. Microphone access requires localhost or HTTPS.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | unset | Required server-side API key for all Gemini calls |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model for audio transcription and code analysis; use a model available to your API key |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | Gemini speech-generation model available to your API key |
| `GEMINI_TTS_VOICE` | `Kore` | Gemini TTS voice |
| `GEMINI_TIMEOUT_S` | `90` | Request timeout |
| `MAX_UPLOAD_MB` | `8` | Maximum audio upload size |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `RATE_LIMIT_PER_HOUR` | `8` | Requests per visitor per hour; `0` disables it |
| `DAILY_CAP` | `200` | Total requests per server process per day; `0` disables it |
| `OWNER_KEY` | unset | Optional secret accepted in the `X-Owner-Key` header to bypass request limits |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Local server bind address |
| `LOG_LEVEL` | `INFO` | Python log level |

Restart the server after changing `.env`. Never put the key in `index.html`, a URL, or source control. The included `.gitignore` excludes `.env`.

## Deploying

For Render, create a Python web service with:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Add `GEMINI_API_KEY` and any optional settings in the service's environment dashboard. Use an actual Gemini API key there; do not commit it.

## API

FastAPI's interactive docs are available at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and model configuration status; never returns keys |
| `GET /api/config` | UI profiles and model names |
| `GET /api/models` | Gemini models accessible to the configured key, including supported generation methods |
| `POST /api/transcribe` | Audio to transcript |
| `POST /api/analyze` | Transcript and editor context to structured diagnosis |
| `POST /api/speak` | Text to WAV or SSE audio events |
| `POST /api/debug` | Full voice/typed pipeline over SSE |
| `WS /ws/debug` | Context, audio, and typed requests over WebSocket |

`POST /api/debug` accepts multipart form fields `audio` or `transcript`, `profile`, `file_name`, `language`, `code`, `error_log`, optional `cursor_line`, and optional `speak_reply`.

If a model is unavailable to the configured key, the API reports `MODEL_NOT_AVAILABLE`. Open `/api/models` on your running server and set `GEMINI_MODEL` and/or `GEMINI_TTS_MODEL` to a listed model that supports the needed operation. Use the model ID (for example `gemini-3.8-flash`), not the Google Cloud project ID. A `models/` resource-name prefix is accepted and removed automatically.

## Known limitations

- The suggested fix is generated by a model; verify it and run your tests before using it.
- Responses depend on the context provided. The current UI is a mock editor and does not automatically read a real IDE's files or diagnostics.
- Each question is independent; the model has no conversation memory.
- Gemini TTS is generated as a complete response before the browser starts playback, rather than streamed as it is generated.
- The rate limiter is held in memory and resets when the process restarts. It is not a substitute for authentication or persistent quota controls.
- Gemini API access, available models, quotas, and pricing depend on your Google account and current API configuration.
- Audio, transcripts, code, and error output are sent to Google Gemini for processing. Do not send secrets or proprietary code unless you are comfortable with that.

## Tests

Run the unit tests from the project root:

```powershell
python -m unittest discover -s tests -v
```

The tests mock Gemini's HTTP responses and do not require a key or make external calls.

## Project files

- `main.py` — Gemini client, FastAPI routes, prompt, and pipeline.
- `index.html` — mock editor, recorder, and audio player.
- `.env.example` — local environment template.
- `settings-template.txt` — deploy settings reference.
- `requirements.txt` — Python dependencies.
- `LICENSE` — MIT licence.
