# Dhwani-Code (ध्वनि)

Talk to your debugger in Hinglish or Tamil. You speak about an error while looking at your code, and Dhwani-Code answers out loud with the exact line to change.

```
 editor context (code + stack trace) ──────────────┐
                                                   ▼
 mic ──► Prisma STT ──► transcript ──► Evon reasoning ──► Timbre TTS ──► speaker
        (keyterms from                 (JSON: cause, line,    (streamed PCM)
         your own code)                 fix, spoken script)
```

This repo is the backend (FastAPI) and a split-screen web UI that stands in for the IDE: a mock editor with buggy snippets on the left, the voice panel on the right. The backend also exposes a WebSocket so a real VS Code extension can use it later.

## Read this first: what runs where

| Stage | Model | Where it runs | Uses your 5,000 credits? |
|---|---|---|---|
| Speech to text | Prisma v2.5 (default) or v3.0 | Gnani API, `api.vachana.ai` | Yes |
| Reasoning | Evon v3.3 (30B MoE, 3.5B active) | **Your own server.** Evon v3.3 is open weights (Apache 2.0) and is not on the Gnani API platform | No |
| Text to speech | Timbre v2.5 (default) or v3.0 | Gnani API | Yes |

Two things differ from the usual assumption, so the code handles them explicitly:

1. **Evon is not an API endpoint you can call with `GNANI_API_KEY`.** The backend talks to any OpenAI-compatible server (vLLM or SGLang) set in `EVON_BASE_URL`. Until you host it, leave that empty and a small **demo analyzer** answers instead. It only understands the three bundled bug patterns (C++ off-by-one, Python `range(len(x)+1)`, null dereference in a `while`), but it lets you test the whole voice loop with real Prisma and Timbre.
2. **Prisma v2.5 has no `keyterms` parameter.** `keyterms` belongs to Prisma v3.0. For v2.5 the backend converts your keyterms into `bias_list` (single words, letters only, up to 100) with `bias_score`. It also adds words from the code you are looking at, split from `camelCase` and `snake_case`, so `sumReadings` boosts "sum" and "readings". Set `STT_MODEL=prisma-v3.0` to send real `keyterms`, a `topic`, and `format=codemix` instead.

## Quick start

Needs Python 3.10+ and a recent Chrome, Edge, Firefox or Safari.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # Windows: copy .env.example .env
# edit .env and set GNANI_API_KEY (create one at https://app.gnani.ai/voice)

python main.py
```

Open <http://127.0.0.1:8000>, tap the mic, ask about the error, and tap again to send. Space also toggles recording. Microphone access works on `localhost` and `https` only.

The header shows what is live: a green dot per model that is ready, an amber dot for "Evon demo rules".

If you cannot use the mic, type into the box at the bottom of the voice panel. Typed questions skip Prisma but still use Evon and Timbre.

## Hosting Evon v3.3

You need a GPU box (the model card lists H100 80GB, H200 and A100). From the model card, adapted so it does not collide with this app's port 8000:

```bash
pip install -U "vllm>=0.12.0"
vllm serve gnani/gnani-evon-v3.3-30B-A3B \
  --served-model-name gnani-evon-v3.3 \
  --max-model-len 32768 \
  --port 8001 \
  --trust-remote-code \
  --reasoning-parser nano_v3
```

Use the repo id shown on the model's Hugging Face page, and request access there if it is gated. Then in `.env`:

```
EVON_BASE_URL=http://<gpu-host>:8001/v1
EVON_MODEL=gnani-evon-v3.3
```

The backend sends the model card's recommended sampling (`temperature 0.6`, `top_p 0.95`) with thinking **off** (`chat_template_kwargs.enable_thinking=false`), because reasoning traces add seconds before the first spoken word. Set `EVON_ENABLE_THINKING=true` if you prefer accuracy over speed.

Quantized builds exist for llama.cpp, Ollama and LM Studio if you want to try it on a smaller machine. Check that they still return clean JSON before trusting them. Any other OpenAI-compatible endpoint also works as a stand-in while you wait for GPU access: if the server rejects the vLLM-only fields, the backend retries without them.

## Configuration

All settings live in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `GNANI_API_KEY` | none | Required for speech. Sent as the `X-API-Key-ID` header |
| `GNANI_MAX_CONCURRENCY` | `1` | Some accounts allow one active request at a time (HTTP 429 otherwise) |
| `STT_MODEL` | `prisma-v2.5` | `prisma-v2.5` or `prisma-v3.0` |
| `STT_BIAS_SCORE` | `1.0` | Word-boost strength for v2.5: 0, 0.5, 1, 1.5 or 2. Raise only if jargon is still missed |
| `TTS_MODEL` | `timbre-v2.5` | `timbre-v2.5` or `timbre-v3.0` |
| `TTS_VOICE` | per language | Empty picks Poorvi (Hinglish), Nalini (Hindi), Noopur (Tamil) on v2.5, or Shivani on v3.0. Voices are not shared between Timbre versions |
| `TTS_SPEED` | `1.0` | 0.85 to 1.15 |
| `TTS_SAMPLE_RATE` | `24000` | 8000, 16000, 22050, 24000, 44100 or 48000 |
| `EVON_BASE_URL` | empty | OpenAI-compatible base URL including `/v1`. Empty means demo analyzer |
| `EVON_MODEL` | `gnani-evon-v3.3` | Must match `--served-model-name` |
| `EVON_ENABLE_THINKING` | `false` | Reasoning traces on or off |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Server address |
| `CORS_ORIGINS` | `*` | Comma-separated. Tighten it before deploying |

### Spoken languages

The language switch in the UI (the `profile` field in the API) sets the whole chain:

| Profile | Prisma v2.5 | Reply style from Evon | Timbre v2.5 |
|---|---|---|---|
| `hinglish` | `en-IN` (accepts Hindi-English mixed audio, Latin output) | Hinglish in Roman script | `hi-en`, Poorvi |
| `hindi` | `hi-IN` | Hindi in Devanagari, English tech terms | `hi-IN`, Nalini |
| `tanglish` | `ta-IN` | Tamil script, English tech terms | `ta-IN`, Noopur |

Prisma v3.0 gets language hints instead (for example `hi-IN,en-IN`).

## API

Interactive docs are at <http://127.0.0.1:8000/docs>. A few things to know:

- Audio must be WAV, MP3, OGG, FLAC, AAC or M4A, at most 30 seconds. Prisma rejects the WebM that Chrome's `MediaRecorder` produces, so the web UI re-encodes every recording to 16 kHz mono WAV in the browser before uploading.
- Errors on plain endpoints look like `{"error": {"stage", "code", "message"}}`. On streams they arrive as an `error` event.

| Endpoint | Does |
|---|---|
| `GET /api/health` | Status and which models are active (never returns the key) |
| `GET /api/config` | Profiles, models, default keyterms (used by the UI) |
| `POST /api/transcribe` | Audio in, transcript out (Prisma only) |
| `POST /api/analyze` | Transcript and editor context in, structured fix out (Evon only) |
| `POST /api/speak` | Text in, speech out (Timbre only). `"format": "sse"` streams, `"wav"` returns one file |
| `POST /api/debug` | The full loop as one SSE stream |
| `WS /ws/debug` | Same loop over a long-lived socket for IDE extensions |

### Try each stage

```bash
curl localhost:8000/api/health

# Timbre only: hear a Hinglish line
curl -X POST localhost:8000/api/speak -H 'Content-Type: application/json' \
  -d '{"text":"Line 42 me loop condition less than size karo.","profile":"hinglish","format":"wav"}' -o out.wav

# Prisma only (the code field biases recognition towards your identifiers)
curl -X POST localhost:8000/api/transcribe -F audio=@question.wav -F profile=hinglish -F 'code=<main.cpp'

# Evon only
curl -X POST localhost:8000/api/analyze -H 'Content-Type: application/json' -d '{
  "transcript": "yaar segmentation fault kyu aa raha hai",
  "context": {"file_name": "main.cpp", "language": "C++", "code": "...", "error_log": "..."},
  "profile": "hinglish"}'

# Everything, with a typed question instead of audio. Use -N so the stream is not buffered.
curl -N -X POST localhost:8000/api/debug \
  -F transcript='bhai ye loop me kya galat hai' -F file_name=main.cpp -F language='C++' \
  -F 'code=<main.cpp' -F 'error_log=<crash.log' -F speak_reply=false
```

### Event stream

`/api/debug` and `/ws/debug` emit the same events, in order:

| Event | Payload |
|---|---|
| `status` | `stage`: `stt`, `analysis` or `tts` |
| `transcript` | `text`, `model`, `latency_ms` |
| `fix` | `fix` (`root_cause`, `line_to_fix`, `suggested_code`, `speech_script`, `confidence`), `spoken` (the cleaned text sent to Timbre), `provider` (`evon` or `demo`), `latency_ms` |
| `audio_start` | `sample_rate`, `channels: 1`, `encoding: "pcm_s16le"`, `text` |
| `audio` | SSE: `seq`, `data` (base64 PCM). WebSocket: a binary frame of raw PCM |
| `done` | `request_id`, `timings` (`stt_ms`, `evon_ms`, `tts_first_audio_ms`, `total_ms`) |
| `error` | `stage`, `code`, `message` |

The speech script is cleaned before synthesis: operators become words ("<=" becomes "less than or equal to"), and in Roman-script replies digits become words ("line 42" becomes "line forty two"), because Timbre mispronounces raw symbols and numbers.

### WebSocket protocol (for the VS Code extension)

```
client → {"type":"context","file_name","language","code","error_log","cursor_line","profile"}
         send again whenever the editor state changes
client → binary frames with one audio clip, then {"type":"end_audio"}
client → {"type":"ask","text":"..."}      typed question, skips speech recognition
client → {"type":"reset"}                 drop buffered audio
server → JSON events as above; audio chunks arrive as binary frames after audio_start
```

```js
// Node, using the "ws" package
const ws = new WebSocket("ws://127.0.0.1:8000/ws/debug");
ws.on("open", () => {
  ws.send(JSON.stringify({ type: "context", file_name: "main.cpp", language: "C++", code, error_log, cursor_line: 42, profile: "hinglish" }));
  ws.send(fs.readFileSync("question.wav"));
  ws.send(JSON.stringify({ type: "end_audio" }));
});
ws.on("message", (data, isBinary) => (isBinary ? playPcm(data) : console.log(JSON.parse(data.toString()))));
```

For a real extension: build the `context` message from the active text editor and `vscode.languages.getDiagnostics()` or the terminal output, record the mic and play audio inside a webview (the extension host itself has no audio access), and reuse the recorder and PCM player code from `index.html`.

## How it works

- **Keyterms from your code.** Before each Prisma call, the backend builds a word list from programming vocabulary (vector, segmentation fault, nullptr, ...) plus the most frequent identifiers in the file you are looking at.
- **Evon prompt.** The system prompt makes Evon a senior engineer who answers with one JSON object and a script of at most two short sentences. The code goes in with line numbers so Evon can cite exact lines, and it is told to name the line that is wrong, which may differ from the line that crashes. Replies are parsed leniently (think blocks, code fences and stray text are stripped). If the JSON is unusable, the plain text is spoken at low confidence instead of failing.
- **Streaming audio.** Timbre's SSE stream is requested as raw PCM, relayed chunk by chunk, and scheduled into Web Audio as it arrives, so playback starts before synthesis finishes. Replay uses the buffered chunks and costs no extra credits.
- **Credits.** One voice question costs one Prisma call and one Timbre call. Keep scripts short, and use the typed box or the `/api/analyze` endpoint while you iterate on prompts.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "GNANI_API_KEY is not set" | `.env` is missing or you did not restart after editing it |
| 401, `INVALID_API_KEY` | Wrong key, or a stray space or quote in `.env` |
| 402 or 403 | Out of credits, or the key lacks the STT or TTS scope |
| 429, `RATE_LIMITED` | Your account allows one active request at a time. Keep `GNANI_MAX_CONCURRENCY=1` and wait for the current request |
| "No speech was detected" | Recording too short or too quiet. Check the level meter moves while you speak |
| "Audio is longer than 30 s" | Prisma's REST limit. The UI stops at 28 s on its own |
| `EVON_NOT_CONFIGURED` | Demo analyzer only knows three bugs. Host Evon and set `EVON_BASE_URL` |
| `EVON_UNREACHABLE` | The vLLM or SGLang server is down, or the URL is missing `/v1` |
| Transcript has the wrong jargon | Raise `STT_BIAS_SCORE` to 1.5, or try `STT_MODEL=prisma-v3.0` |
| No sound but the fix appears | Browser blocked audio. Tap the mic once (that unlocks playback), or use the replay button |

## Files

```
main.py            FastAPI app: config, Prisma/Evon/Timbre clients, pipeline, routes
index.html         Web UI: mock IDE, MediaRecorder capture, SSE reader, PCM player
requirements.txt   Python dependencies
.env.example       Every setting, documented
```

## Before you ship it

- Set `CORS_ORIGINS` to your real origin. The default `*` is for local development.
- The API has no user authentication. Do not expose it publicly with your key behind it.
- Prisma's own docs say transcripts should be checked before high-stakes use, and Evon's card says it is not for high-stakes decisions. Treat its fixes as suggestions you review.
