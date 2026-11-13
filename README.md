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
5. Highlights pasted code and runs JavaScript, Python, C, and C++ locally in the browser. The browser runner needs no compiler API key or separate execution server.

Gemini is used for speech-to-text, code analysis, and text-to-speech. The Gemini API key stays on the server.

The browser runner executes JavaScript in a restricted iframe, Python with Pyodide in a Web Worker, C with Clang in the Wasmer SDK, and C++ with Runno's WebAssembly Clang++ runtime. The runtimes/compiler are downloaded from jsDelivr, Wasmer, or Runno on first use; program source and standard input stay on the user's device. Java, Go, and Rust remain syntax-highlighted but are not executable in the browser runner.

The backend's optional `POST /api/run` Judge0 integration remains available for deployments that configure a separate Judge0 service. It is not required by the browser Run button.

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
| Frontend | `index.html`, plain JavaScript, Web Audio; browser-side language runners |
| Hosting | Render or another Python ASGI host |

The browser records audio and converts it to 16 kHz mono WAV. The backend sends it to Gemini, then sends the transcript and editor context for analysis. Gemini TTS returns WAV audio; the backend converts it to PCM for the existing SSE/WebSocket audio player. TTS is generated before the audio event is sent, so playback starts after that generation call completes.

Transient Gemini server errors (including temporary high-demand responses) are retried up to twice with a short backoff. Persistent overload is reported as a temporary service error; retry later rather than changing the model or API key.

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
| `JUDGE0_API_KEY` | unset | Optional server-side key/token for the backend's Judge0 API |
| `JUDGE0_API_URL` | `https://judge0-ce.p.rapidapi.com` | Optional Judge0 API base URL for the backend endpoint |
| `MAX_UPLOAD_MB` | `8` | Maximum audio upload size |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `RATE_LIMIT_PER_HOUR` | `8` | Requests per visitor per hour; `0` disables it |
| `DAILY_CAP` | `200` | Total requests per server process per day; `0` disables it |
| `OWNER_KEY` | unset | Optional secret accepted in the `X-Owner-Key` header to bypass request limits |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Local server bind address |
| `LOG_LEVEL` | `INFO` | Python log level |

Restart the server after changing `.env`. Never put the key in `index.html`, a URL, or source control. The included `.gitignore` excludes `.env`.

## Self-hosting Judge0

Judge0 runs separately from this application and is not required for the browser runner. The upstream [deployment procedure](https://github.com/judge0/judge0/blob/master/CHANGELOG.md#deployment-procedure) states that Judge0 is tested on Linux; its isolated execution requires Linux cgroups. Windows Docker Desktop/WSL is not a supported production host and may start the containers while program execution still fails.

On a Linux machine, clone the official [Judge0 repository](https://github.com/judge0/judge0) outside this app's project folder. Before starting it, set strong `REDIS_PASSWORD` and `POSTGRES_PASSWORD` values in `judge0.conf`; enable `AUTHN_HEADER=X-Auth-Token` and set a strong `AUTHN_TOKEN` when the API will be reachable outside the machine. Follow the upstream startup steps, which start the `db` and `redis` services before the full Compose stack.

For an app running on the same Linux host, set `JUDGE0_API_URL=http://localhost:2358`. For an app deployed on Render, **do not use `localhost`**: that points to the Render app container, not your server. Give the Linux server a DNS name, put Judge0 behind an HTTPS reverse proxy, and set `JUDGE0_API_URL` in Render to that HTTPS API URL. If Judge0 token authentication is enabled, set `JUDGE0_API_KEY` to the same token; the backend sends it as `X-Auth-Token`. Do not expose an unauthenticated compiler publicly.

The app only permits HTTPS for remote Judge0 URLs (plain HTTP is permitted for loopback development), so it rejects a remote `http://your-server-ip:2358` URL. The RapidAPI URL remains the default; override it with your own Judge0 URL to use the self-hosted service.

The backend fetches `/languages` and selects the available Judge0 language IDs, so it can work with different IDs across self-hosted and hosted Judge0 releases.

## Deploying

This repository includes a Render Blueprint in [`render.yaml`](./render.yaml). To deploy it, connect this Git repository to Render and create a Blueprint instance. Render will install the requirements and run Uvicorn bound to `0.0.0.0` on Render's assigned `$PORT`. Add `GEMINI_API_KEY` in Render's environment settings; the browser runner does not need a Judge0 key.

For an existing Render web service, deploy the latest commit from this repository's branch and set `GEMINI_API_KEY` in **Dashboard → Environment**. Add Judge0 settings only if you intend to use the optional backend endpoint. Confirm the service uses this repository's root directory and the following commands:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

After deployment, `/api/health` should report `gemini_key_configured: true` and Gemini model IDs, while `/api/models` should return the models accessible to that key. If `/api/models` returns 404 or health reports another provider, the Render service is still running an older deployment or the wrong repository/branch.

## API

FastAPI's interactive docs are available at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and model configuration status; never returns keys |
| `GET /api/config` | UI profiles and model names |
| `GET /api/models` | Gemini models accessible to the configured key, including supported generation methods |
| `POST /api/run` | Compile and run one of the supported languages in Judge0 |
| `POST /api/transcribe` | Audio to transcript |
| `POST /api/analyze` | Transcript and editor context to structured diagnosis |
| `POST /api/speak` | Text to WAV or SSE audio events |
| `POST /api/debug` | Full voice/typed pipeline over SSE |
| `WS /ws/debug` | Context, audio, and typed requests over WebSocket |

`POST /api/debug` accepts multipart form fields `audio` or `transcript`, `profile`, `file_name`, `language`, `code`, `error_log`, optional `cursor_line`, and optional `speak_reply`.

`POST /api/run` accepts JSON fields `language`, `code`, and optional `stdin`. If configured, source code and standard input are sent to Judge0 for execution. The editor's browser runner does not call this endpoint.

If a model is unavailable to the configured key, the API reports `MODEL_NOT_AVAILABLE`. Open `/api/models` on your running server and set `GEMINI_MODEL` and/or `GEMINI_TTS_MODEL` to a listed model that supports the needed operation. Use the model ID (for example `gemini-3.8-flash`), not the Google Cloud project ID. A `models/` resource-name prefix is accepted and removed automatically.

## Known limitations

- The suggested fix is generated by a model; verify it and run your tests before using it.
- Responses depend on the context provided. The current UI is a mock editor and does not automatically read a real IDE's files or diagnostics.
- Each question is independent; the model has no conversation memory.
- Browser-side execution uses the visitor's device and is not a substitute for server-side isolation for untrusted multi-user workloads.
- C and C++ compilation require internet access and a browser with WebAssembly threads and `SharedArrayBuffer` support. The FastAPI root page sets the required cross-origin-isolation headers. The C runner downloads the pinned Wasmer SDK/Clang package; the C++ runner downloads the pinned Runno runtime and its Clang++ assets. Both compile and execute in browser WebAssembly sandboxes rather than a remote code-execution service. Runno is MIT licensed; the Wasmer SDK uses a modified MIT license with a branding requirement above its published usage thresholds.
- JavaScript, Python, C, and C++ source and standard input are executed or compiled on the visitor's device, not sent to a compiler service. Python, C, and C++ runtimes/toolchains download from jsDelivr, Wasmer, or Runno on first use.
- Gemini TTS is generated as a complete response before the browser starts playback, rather than streamed as it is generated.
- The rate limiter is held in memory and resets when the process restarts. It is not a substitute for authentication or persistent quota controls.
- Gemini API access, available models, quotas, and pricing depend on your Google account and current API configuration.
- Audio, transcripts, code, and error output used for AI debugging are sent to Google Gemini. Browser-run code and stdin are executed locally; compiler/runtime bundles are downloaded from jsDelivr, Wasmer, or Runno. Do not send secrets or proprietary code to Gemini unless you are comfortable with that service receiving it.

## Tests

Run the unit tests from the project root:

```powershell
python -m unittest discover -s tests -v
```

The tests mock Gemini and Judge0 HTTP responses; they do not require keys or make external calls.

## Project files

- `main.py` — Gemini and Judge0 clients, FastAPI routes, prompt, and pipeline.
- `index.html` — syntax-highlighted editor, compiler controls, recorder, and audio player.
- `.env.example` — local environment template.
- `settings-template.txt` — deploy settings reference.
- `requirements.txt` — Python dependencies.
- `LICENSE` — MIT licence.
