# Dhwani-Code (ध्वनि)

**Debug in the language you think in.**

Dhwani-Code is a voice debugger for Indian developers. You look at an error, ask about it out loud in Hinglish, Tanglish, Benglish or Hindi, and it answers out loud: it names the exact line, highlights it in the code, shows the corrected line, and speaks a short explanation in the same language mix.

- Live demo: <https://dhwani-code-voice-debugging.onrender.com> (free hosting, so the first visit after a pause takes about a minute to wake up)
- Demo video: <https://youtu.be/i5sSHQD05Xw>
- Built solo by Riya Tiwari ([@anilendu007](https://github.com/anilendu007)) for the Great Indian AI Internship Challenge by Gnani AI.
- Licence: MIT (see `LICENSE`).

## Contents

1. [What it does](#1-what-it-does)
2. [Why it exists](#2-why-it-exists)
3. [What is built on what](#3-what-is-built-on-what)
4. [Architecture and data flow](#4-architecture-and-data-flow)
5. [Languages](#5-languages)
6. [How the interesting parts work](#6-how-the-interesting-parts-work)
7. [Quick start (run it locally)](#7-quick-start-run-it-locally)
8. [Using the app](#8-using-the-app)
9. [Configuration reference](#9-configuration-reference)
10. [Deploying on Render](#10-deploying-on-render)
11. [Choosing and changing the reasoning model](#11-choosing-and-changing-the-reasoning-model)
12. [API reference](#12-api-reference)
13. [Security and privacy](#13-security-and-privacy)
14. [Status, limits and known issues](#14-status-limits-and-known-issues)
15. [Troubleshooting](#15-troubleshooting)
16. [Manual test plan](#16-manual-test-plan)
17. [Project structure](#17-project-structure)
18. [Roadmap](#18-roadmap)
19. [Challenge submission summary](#19-challenge-submission-summary)
20. [Credits](#20-credits)

---

## 1. What it does

1. The page shows a mock editor on the left: code, plus the error output a real IDE would capture (stack trace, sanitizer report, debugger output).
2. You tap the microphone and ask a question in your own words, for example: *"Yaar sumReadings me heap buffer overflow kyu aa raha hai?"*
3. The backend turns your speech into text, sends the text together with the code and the error to a reasoning model, and turns the model's short answer into speech.
4. You hear the answer while the page shows what you said, the pink line that is wrong, the corrected line in green, and the spoken sentence. A replay button plays it again.

It answers what you asked, not just the bug. Three kinds of question are handled:

| You ask | You get |
|---|---|
| A bug or fix question ("why does this crash?") | The root cause, the single line to change, the corrected line, and a two-sentence spoken answer |
| An explanation question ("what is a heap buffer overflow?") | A plain-language explanation using your own code as the example, and no code change |
| A follow-up or opinion ("is this efficient?") | A direct answer, with a line and code only if it recommends a change |

You can use the three bundled bugs, or paste your own code and error message in the **Your code** tab.

## 2. Why it exists

Developers in India usually think and talk about bugs in a mix of English with Hindi, Tamil, Bengali or another language. Almost every debugging tool expects typed English. When something crashes, the usual loop is to leave the editor, translate the problem into a search query, read pages, and come back. Dhwani-Code keeps you in the editor and in your own language: you ask the way you would ask a colleague at the next desk, and you hear the answer.

The hard part is not speech recognition in general. It is **code-mixed speech full of programming vocabulary**: "vector", "segmentation fault", `findNode`, said in the middle of a Hindi, Tamil or Bengali sentence. Section 6 explains how the project handles that.

## 3. What is built on what

| Stage | Model or service | How this project uses it |
|---|---|---|
| Speech to text | **Gnani Prisma v2.5** (Gnani API) | Hinglish and Hindi. Word boosting (`bias_list`) built from programming vocabulary and from identifiers in the code on screen |
| Speech to text, romanised | **Gnani Prisma v3.0** (Gnani API) | Tanglish and Benglish. `format=transliterate` returns the transcript in Latin letters. Real `keyterms` and a `topic` are sent. Falls back to Prisma v2.5 if v3.0 refuses the request |
| Reasoning | **openai/gpt-oss-120b**, served by Groq through an OpenAI-compatible API | Reads the transcript, the code with line numbers and the error log, and returns a JSON answer |
| Text to speech | **Gnani Timbre v2.5** (Gnani API) | Streams the spoken answer over Server-Sent Events as raw PCM. Voices: Poorvi (Hinglish), Noopur (Tamil), Kirra (Bengali), Nalini (Hindi) |
| Backend | Python, FastAPI, httpx | Orchestrates the three stages and streams events to the page |
| Frontend | One HTML file, plain JavaScript | MediaRecorder capture, Server-Sent Events reader, Web Audio playback |
| Hosting | Render (free web service) | Serves the page and the API from one address |

**About Evon.** Gnani's Evon v3.3 is an open-weights model (Apache 2.0) that is not on Gnani's API platform, so it cannot be called with a Gnani API key. It has to be requested on Hugging Face and hosted on your own GPU. This project did not use it. The reasoning step is written against the OpenAI-compatible chat API, so the model is a configuration choice: pointing it at a self-hosted Evon server later means changing three settings and no code (section 11). The page always shows the name of the model that is really answering, and it says "Demo rules" if none is connected.

## 4. Architecture and data flow

```
 ┌──────────────────────────────── Browser ────────────────────────────────┐
 │  mock editor (code + error log)          voice panel                    │
 │         │                                 │ mic (MediaRecorder)         │
 │         │                                 │ → decode → 16 kHz mono WAV  │
 └─────────┼─────────────────────────────────┼─────────────────────────────┘
           │   multipart POST /api/debug (audio + code + error log)
           ▼
 ┌────────────────────────────── FastAPI backend ──────────────────────────┐
 │  rate limit → validate audio → build keyterms from the code             │
 │        │                                                                 │
 │        ├─► Prisma STT (Gnani)  ──► transcript                           │
 │        ├─► Reasoning model (Groq, gpt-oss-120b) ──► JSON answer         │
 │        └─► Timbre TTS (Gnani, SSE) ──► raw PCM chunks                   │
 └─────────────────────────────────────┬────────────────────────────────────┘
                                       │  Server-Sent Events stream
                                       ▼
   status → transcript → fix → audio_start → audio … audio → done
                                       │
                          browser highlights the line, plays PCM as it arrives
```

Step by step for one voice question:

1. **Record.** The page records with the MediaRecorder API, decodes the result and re-encodes it as 16 kHz mono WAV in the browser. Prisma does not accept the WebM that Chrome records, so every browser is normalised to WAV. Recording stops by itself at 28 seconds (Prisma's REST limit is 30).
2. **Upload.** The page posts audio, language profile, file name, programming language, code and error log to `POST /api/debug`.
3. **Guard.** The backend checks the per-visitor and daily limits, then validates the audio (format, size, duration).
4. **Keyterms.** It builds a vocabulary list from programming terms plus identifiers in the code you are looking at.
5. **Transcribe.** It calls Prisma. Hinglish and Hindi use v2.5. Tanglish and Benglish use v3.0 and fall back to v2.5.
6. **Reason.** It sends the transcript, the numbered code and the error log to the reasoning model with a system prompt that asks for one JSON object.
7. **Clean up the script.** The spoken sentence is normalised for text to speech (operators spelled out, numbers as words for Hinglish).
8. **Speak.** It calls Timbre and relays each audio chunk to the page as it arrives.
9. **Play and show.** The page schedules each chunk into Web Audio immediately, so sound starts before synthesis finishes, and it highlights the faulty line with the suggested fix underneath.

The stage list on the page shows how long each stage took, and the last line shows the time to first sound.

## 5. Languages

Gnani's APIs have no code-mixed language codes for Tamil or Bengali, and Timbre has a code-mixed voice only for Hinglish. The four profiles work around this as follows. The profile is selected with the buttons at the top of the voice panel (the `profile` field in the API).

| Profile | Speech in (Prisma) | Reply written by the model | Speech out (Timbre v2.5) |
|---|---|---|---|
| `hinglish` | v2.5, `en-IN`. Prisma's English accepts Hindi-English mixed audio and returns Latin letters | Hinglish in Roman script | `hi-en`, voice Poorvi |
| `tanglish` | **v3.0**, `ta-IN,en-IN`, `format=transliterate`: transcript in Latin letters | Tamil in Tamil script, English terms left in English | `ta-IN`, voice Noopur |
| `benglish` | **v3.0**, `bn-IN,en-IN`, `format=transliterate` | Bengali in Bengali script, English terms left in English | `bn-IN`, voice Kirra |
| `hindi` | v2.5, `hi-IN` | Hindi in Devanagari, English terms left in English | `hi-IN`, voice Nalini |

Design choices behind this:

- **Why the Tamil and Bengali replies are in native script.** Timbre's voices are tuned for each language's own script. Roman-script Tamil or Bengali risks being read with English pronunciation. English technical terms stay in Latin letters inside the sentence, which is how the language mix sounds when spoken.
- **Why Tanglish and Benglish force Prisma v3.0.** The `transliterate` format, which writes the whole transcript in Latin letters, exists only on v3.0. If your key cannot use v3.0 (a model, scope or format error), the backend retries once on v2.5 with `ta-IN` or `bn-IN`, and the transcript then arrives in native script.
- **Numbers.** For Hinglish, line numbers are converted to words ("line eight") before synthesis, because raw digits and symbols are easy for text to speech to mispronounce.

## 6. How the interesting parts work

### 6.1 Vocabulary boosting from your own code

Generic speech models mangle programming words when they sit inside another language. Before every Prisma call the backend builds a vocabulary list from two sources:

- **A fixed list** of about 40 debugging terms: vector, segmentation fault, nullptr, stack trace, out of bounds, off by one, dangling pointer, memory leak, IndexError, TypeError and so on.
- **The code on screen.** The most frequent identifiers are extracted and split on `snake_case` and `camelCase`, so `sumReadings` contributes "sum" and "readings". A stop-word list removes common keywords such as `for`, `int` and `return`.

What is sent depends on the Prisma generation:

| Prisma | Parameter | Rules |
|---|---|---|
| v2.5 | `bias_list` with `bias_score` | Single words, letters only, at most 100. One invalid entry makes the API ignore the whole list, so the builder is strict. `bias_score` is rounded to 0, 0.5, 1, 1.5 or 2 |
| v3.0 | `keyterms` and `topic` | Phrases allowed, up to 120 sent. The topic describes the situation (a developer debugging code in this file, speaking code-mixed English) and quotes the first line of the error |

### 6.2 The reasoning prompt

The system prompt turns the model into a senior engineer helping by voice. It tells the model to:

- decide whether the question is a bug question, an explanation, a follow-up, or off topic, and answer that;
- use the error log first, then the code, and trust the line numbers (the code is sent as `  8 | for (...)`, with a number on every line);
- name the line that is *wrong*, which can differ from the line where the program *crashes*;
- return exactly one JSON object: `root_cause`, `line_to_fix`, `suggested_code`, `speech_script`, `confidence`;
- write the spoken script in the chosen language style, at most two short sentences for a fix and three for an explanation, with no symbols or code blocks.

Each profile adds a style instruction and one example sentence in its language. The transcript may be romanised Hindi, Tamil or Bengali, and the prompt says so.

Replies are parsed leniently. Thinking blocks, markdown fences and stray text around the JSON are stripped. If the JSON is unusable, the plain text is spoken at low confidence instead of failing.

### 6.3 Speaking: streaming audio

Timbre's Server-Sent Events stream is requested as raw 16-bit mono PCM at 24 kHz. The backend relays each chunk to the page as soon as it arrives. The page converts each chunk to samples and schedules it on the Web Audio timeline right away, carrying over a stray byte when a chunk boundary falls inside a sample. Replay uses the buffered chunks and costs no extra credits.

### 6.4 Honest labelling

The header lists the models that are really in use: Prisma, the reasoning model's configured name, and Timbre. When no reasoning model is connected, a small rule-based fallback answers instead and the page says so ("Demo rules", and "Answered by built-in demo rules, not an AI model" under the fix). Under every model answer the page prints "Reasoning by <model name>".

### 6.5 Protecting credits on a public link

The app has no login, so two in-memory limits stop a public link from draining the Gnani credits: a per-visitor hourly limit and a daily cap for everyone. The owner can bypass them with a secret key. Details are in section 9.

### 6.6 Demo rules (offline fallback)

If no reasoning model is configured, a tiny rule-based analyser recognises exactly three bug patterns (a `<=` loop bound, `range(len(x) + 1)`, and a pointer dereferenced in a `while` condition) and replies in the chosen language. It is not an AI model, it ignores what you actually asked, and it exists only so the voice loop can be tested without a reasoning model.

## 7. Quick start (run it locally)

You need Python 3.10 or newer and a recent Chrome, Edge, Firefox or Safari.

```bash
# 1. Get the files (unzip, or clone your repo) and open a terminal in the folder with main.py
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your settings file
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` in a text editor and fill in at least the key:

```
GNANI_API_KEY=your-gnani-key
```

Create the key at <https://app.gnani.ai/voice>. One key covers both Prisma and Timbre. For the reasoning model, also set:

```
EVON_BASE_URL=https://api.groq.com/openai/v1
EVON_API_KEY=your-groq-key
EVON_MODEL=openai/gpt-oss-120b
```

Then start the server and open the address it prints:

```bash
python main.py
# Uvicorn running on http://127.0.0.1:8000
```

Check it:

- Open <http://127.0.0.1:8000/api/health>. You should see `"gnani_key_configured": true`, and `"reasoning": "evon"` with a `reasoning_label` once the reasoning model is set. (`"evon"` is only the internal name for "a reasoning model is configured". The label shows the real model.)
- Open <http://127.0.0.1:8000>. The header shows a green dot for each model that is ready.

Notes:

- Restart the server after every change to `.env`; it is read only at startup.
- The page must be opened from the backend's own address. Opening `index.html` directly, or through a static server, cannot work because the page calls `/api/...` on the same origin.
- Microphone access works on `localhost` and on `https` only.
- If port 8000 is busy, set `PORT=8001` in `.env`.

## 8. Using the app

**Header.** One entry per model with a status dot: green means ready, amber means a fallback is in use (for example "Demo rules"), red means a key is missing. Hover a dot for details.

**Language buttons.** Hinglish, Tanglish, Benglish, Hindi. They change the recognition language, the reply language, the voice and the example question.

**Left side, the mock IDE.**

- Tabs `vector_sum.cpp`, `grades.py` and `linked_list.cpp` hold three bundled bugs, each with a realistic error output in the terminal area below the code.
- **Your code** lets you paste any code and its error message and choose the programming language. Voice and typed questions then use your own snippet. (The faulty line is highlighted in the three bundled files; in the paste view the fix panel shows the old and new line instead.)
- When an answer names a line, that line turns pink, the suggested line appears underneath in green, and small animated bars sit in the gutter while the answer is being spoken.

**Right side, the voice panel.**

- **Microphone.** Tap to start, tap again to send. The space bar does the same when no text box is focused. A live level meter and a timer show while recording. Recording stops by itself at 28 seconds. Tapping the microphone while the app is speaking interrupts it.
- **Progress list.** Transcribing your voice, Finding the bug, Speaking the fix. Each shows its time when done, and the last shows the time to first sound.
- **You said.** The transcript from Prisma.
- **Fix or Answer.** The box says *Fix* when the answer points to one line and *Answer* otherwise. It shows the root cause, the old and new line, the spoken sentence with a replay button, and the line "Reasoning by <model>".
- **Type instead.** A text box at the bottom sends a typed question. It skips speech recognition but still uses the reasoning model and text to speech, so it is useful when the microphone is not available.

Example questions for the bundled bugs:

| File | Bug | Try asking |
|---|---|---|
| `vector_sum.cpp` | Loop uses `i <= size`, reads one element past the end (line 8) | *Yaar sumReadings me heap buffer overflow kyu aa raha hai?* |
| `grades.py` | `range(len(scores) + 1)` indexes past the list (line 3) | *Bhai ye IndexError kyu aa raha hai, loop check karo.* |
| `linked_list.cpp` | `while (cur->value != target)` dereferences null when the value is missing (line 10) | *Bhaiya findNode me segmentation fault kyu aa raha hai?* |

## 9. Configuration reference

Every setting is an environment variable, read from `.env` locally or from the hosting dashboard when deployed. Comments must go on their own line, never after a value.

### Gnani (speech)

| Variable | Default | Meaning |
|---|---|---|
| `GNANI_API_KEY` | none | Required for speech. Sent in the `X-API-Key-ID` header |
| `GNANI_BASE_URL` | `https://api.vachana.ai` | Gnani API address |
| `GNANI_MAX_CONCURRENCY` | `1` | Concurrent Gnani requests. Some accounts allow only one active request at a time |
| `STT_MODEL` | `prisma-v2.5` | `prisma-v2.5` or `prisma-v3.0`. Tanglish and Benglish use v3.0 regardless |
| `STT_BIAS_SCORE` | `1.0` | Word-boost strength for v2.5: 0, 0.5, 1, 1.5 or 2 |
| `TTS_MODEL` | `timbre-v2.5` | `timbre-v2.5` or `timbre-v3.0` |
| `TTS_VOICE` | per language | Empty picks Poorvi, Noopur, Kirra or Nalini on v2.5, or Shivani on v3.0. Setting it forces one voice for every language |
| `TTS_SPEED` | `1.0` | 0.85 to 1.15 |
| `TTS_SAMPLE_RATE` | `24000` | 8000, 16000, 22050, 24000, 44100 or 48000 |

### Reasoning model

The variable names start with `EVON_` for historical reasons. They configure whatever OpenAI-compatible chat model you use.

| Variable | Default | Meaning |
|---|---|---|
| `EVON_BASE_URL` | empty | OpenAI-compatible base URL including `/v1`. Empty means the demo rules |
| `EVON_API_KEY` | empty | The provider's key |
| `EVON_AUTH_HEADER` | `Authorization` | `Authorization` sends `Bearer <key>`; any other name sends the raw key in that header |
| `EVON_MODEL` | `gnani-evon-v3.3` | The model name exactly as the provider lists it, for example `openai/gpt-oss-120b` |
| `REASONING_LABEL` | empty | Name shown in the UI. Empty shows "Evon v3.3" if the model name contains "evon", otherwise the model name |
| `EVON_ENABLE_THINKING` | `false` | Asks vLLM-style servers to switch thinking on or off. Hosted APIs that do not know the field ignore it (see section 11) |
| `EVON_MAX_TOKENS` | empty (600) | Most tokens per answer. Models that think before answering spend part of this budget on thinking, so raise it (for example 2000) if answers come back empty or cut off |
| `EVON_TIMEOUT_S` | `60` | How long to wait for the reasoning model |

### Protecting credits

| Variable | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_PER_HOUR` | `8` | Questions per visitor per hour. `0` turns it off |
| `DAILY_CAP` | `200` | Questions for all visitors together per day (India time). `0` turns it off |
| `OWNER_KEY` | empty | A long random secret. Open `https://your-site/?owner=<key>` once and your tab skips the limits |

The limits are kept in memory. They reset when the server restarts or sleeps, and visitors behind the same address share one allowance.

### Server

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Address for `python main.py`. Hosting platforms usually pass their own port to `uvicorn` instead |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. Tighten it before sharing widely |
| `MAX_UPLOAD_MB` | `8` | Largest audio upload |
| `LOG_LEVEL` | `INFO` | Python log level |
| `RELOAD` | `false` | Set `true` for auto-reload while developing |

## 10. Deploying on Render

The backend serves the page itself, so one web service is enough. A static host such as Vercel or GitHub Pages cannot run the Python backend, and the page would show errors because there is nothing behind `/api/...`.

1. Put these files at the top level of your GitHub repo, with exactly these names: `main.py`, `index.html`, `requirements.txt` (with the final "s"), `.env.example` (with the leading dot), `README.md`. Never commit a real `.env`.
2. In Render choose New, then Web Service, and connect the repo.
3. Settings:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. In the service's **Environment** page, add the variables from section 9 (at least `GNANI_API_KEY`, and `EVON_BASE_URL`, `EVON_API_KEY`, `EVON_MODEL` for the reasoning model). You can add them one by one, paste several lines with "Add from .env", or link an Environment Group on the same page. Keep each variable in one place only: a value set on the service overrides the same name in a group.
5. Save and deploy. Wait until the service says Live.
6. Verify with `/api/health` and the checklist in section 16.

Things to know:

- **Redeploys.** Changing a variable triggers a redeploy. If you push files to GitHub, Render redeploys on its own when auto-deploy is on; otherwise use Manual Deploy, then Deploy latest commit.
- **Free tier sleep.** A free service sleeps after about 15 minutes without traffic, and the next request takes around a minute. Open the link before any demo.
- **Page shows an error right after a deploy.** The service is still starting. Wait a minute and refresh.
- **Logs.** The Logs tab shows one line per request with its timings, or the error code if a stage failed.

## 11. Choosing and changing the reasoning model

The reasoning step only needs an OpenAI-compatible `chat/completions` endpoint.

**Current setup.** `openai/gpt-oss-120b` served by Groq:

```
EVON_BASE_URL=https://api.groq.com/openai/v1
EVON_API_KEY=<Groq key>
EVON_MODEL=openai/gpt-oss-120b
```

`gpt-oss-120b` is a model that thinks before it answers, so it can be slower than a plain instruction model and its thinking counts against the token limit. If you see empty or truncated answers, set `EVON_MAX_TOKENS=2000`. If speed matters more than depth, pick a non-thinking instruction model from your provider's list and change only `EVON_MODEL`.

**Provider compatibility.** The backend sends two vLLM-style extras (`chat_template_kwargs.enable_thinking` and `repetition_penalty`). If the server rejects them with HTTP 400 or 422, the request is retried without them, and from then on the extras are skipped so only the first question pays for the retry.

**Self-hosting Gnani Evon v3.3.** Request the weights on Hugging Face (`gnani/gnani-evon-v3.3-30B-A3B`), then on an 80 GB class GPU:

```bash
pip install -U "vllm>=0.12.0"
vllm serve gnani/gnani-evon-v3.3-30B-A3B \
  --served-model-name gnani-evon-v3.3 \
  --max-model-len 32768 --port 8001 \
  --trust-remote-code --reasoning-parser nano_v3
```

```
EVON_BASE_URL=http://<gpu-host>:8001/v1
EVON_MODEL=gnani-evon-v3.3
```

Use the repository id shown on the model page. Set `REASONING_LABEL=Evon v3.3` if you serve it under another model name.

## 12. API reference

Interactive documentation is generated by FastAPI at `/docs`. Errors on plain endpoints look like this, and on streams they arrive as an `error` event with the same fields:

```json
{"error": {"stage": "stt", "code": "EMPTY_TRANSCRIPT", "message": "No speech was detected. Move closer to the mic and try again."}}
```

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | The web page |
| `GET /api/health` | Status and which models are active. Never returns keys |
| `GET /api/config` | Profiles, model names, limits and default keyterms (used by the page) |
| `POST /api/transcribe` | Audio to transcript (Prisma only) |
| `POST /api/analyze` | Transcript and editor context to structured answer (reasoning model only) |
| `POST /api/speak` | Text to speech (Timbre only) |
| `POST /api/debug` | The whole loop as one Server-Sent Events stream |
| `WS /ws/debug` | The whole loop over a long-lived WebSocket, for an IDE extension |

Every endpoint that spends credits or model calls (`transcribe`, `analyze`, `speak`, `debug`, and the WebSocket) is subject to the limits in section 9. Send the header `X-Owner-Key: <OWNER_KEY>` to bypass them.

### Audio rules

WAV, MP3, OGG, FLAC, AAC or M4A, at most 30 seconds and `MAX_UPLOAD_MB`. WebM is rejected with a clear message. The web page converts its own recordings to WAV.

### `POST /api/transcribe`

Multipart form: `audio` (file), `profile` (default `hinglish`), optional `file_name`, `language`, `code`, `error_log`. Sending `code` lets the backend boost vocabulary from your identifiers.

```bash
curl -X POST http://127.0.0.1:8000/api/transcribe \
  -F audio=@question.wav -F profile=hinglish -F 'code=<main.cpp'
```

Returns `{"transcript": "...", "model": "prisma-v2.5", "latency_ms": 640}`.

### `POST /api/analyze`

```bash
curl -X POST http://127.0.0.1:8000/api/analyze -H 'Content-Type: application/json' -d '{
  "transcript": "yaar segmentation fault kyu aa raha hai",
  "context": {"file_name": "main.cpp", "language": "C++", "code": "...", "error_log": "...", "cursor_line": 10},
  "profile": "hinglish"}'
```

Returns:

```json
{
  "fix": {"root_cause": "...", "line_to_fix": 10, "suggested_code": "...", "speech_script": "...", "confidence": "high"},
  "spoken": "the cleaned text that would be sent to Timbre",
  "provider": "evon",
  "label": "openai/gpt-oss-120b",
  "latency_ms": 1180
}
```

`provider` is `"evon"` when a reasoning model is configured (a historical name) and `"demo"` for the rule-based fallback. `label` is the human-readable model name.

### `POST /api/speak`

```bash
curl -X POST http://127.0.0.1:8000/api/speak -H 'Content-Type: application/json' \
  -d '{"text":"Line 8 me loop condition less than size karo.","profile":"hinglish","format":"wav"}' -o out.wav
```

`"format": "wav"` returns one WAV file. `"sse"` (the default) streams `audio_start`, `audio` and `done` events.

### `POST /api/debug`

Multipart form: `audio` **or** `transcript` (typed question), `profile`, `file_name`, `language`, `code`, `error_log`, optional `cursor_line`, optional `speak_reply` (default true).

```bash
curl -N -X POST http://127.0.0.1:8000/api/debug \
  -F transcript='bhai ye loop me kya galat hai' -F file_name=main.cpp -F language='C++' \
  -F 'code=<main.cpp' -F 'error_log=<crash.log' -F speak_reply=false
```

Use `-N` so curl does not buffer the stream.

### Events (SSE and WebSocket)

| Event | Fields |
|---|---|
| `status` | `stage`: `stt`, `analysis` or `tts` |
| `transcript` | `text`, `model`, `latency_ms` |
| `fix` | `fix` (`root_cause`, `line_to_fix`, `suggested_code`, `speech_script`, `confidence`), `spoken`, `provider`, `label`, `latency_ms` |
| `audio_start` | `sample_rate`, `channels` (1), `encoding` (`pcm_s16le`), `text` |
| `audio` | SSE: `seq` and `data` (base64 PCM). WebSocket: a binary frame of raw PCM |
| `done` | `request_id` and `timings`: `stt_ms`, `evon_ms`, `tts_first_audio_ms`, `total_ms` |
| `error` | `stage`, `code`, `message` |

### WebSocket protocol

```
client → {"type":"context","file_name","language","code","error_log","cursor_line","profile"}
         send again whenever the editor state changes
client → binary frames with one audio clip, then {"type":"end_audio"}
client → {"type":"ask","text":"..."}      a typed question, skips speech recognition
client → {"type":"reset"}                 drop buffered audio
server → the events above; audio chunks arrive as binary frames after audio_start
```

A VS Code extension would build the `context` message from the active editor and its diagnostics, record the microphone in a webview, and reuse the recorder and PCM player code from `index.html`.

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `NO_API_KEY` | 503 | `GNANI_API_KEY` is not set |
| `RATE_LIMITED` | 429 | The visitor or daily limit was reached |
| `UNKNOWN_PROFILE` | 400 | `profile` is not one of the four |
| `INVALID_CONTEXT`, `NO_INPUT` | 400 | Bad context fields, or neither audio nor transcript |
| `EMPTY_AUDIO`, `AUDIO_TOO_LARGE`, `MAX_AUDIO_DURATION_EXCEEDED` | 400, 413 | Audio checks |
| `UNSUPPORTED_AUDIO_FORMAT` | 415 | Not WAV, MP3, OGG, FLAC, AAC or M4A |
| `EMPTY_TRANSCRIPT` | 422 | No speech detected |
| `PRISMA_UNREACHABLE`, `BAD_STT_RESPONSE` | 502 | Speech-to-text service problem |
| `EVON_NOT_CONFIGURED` | 503 | No reasoning model, and the demo rules do not know this code |
| `EVON_UNREACHABLE`, `EVON_HTTP_<status>`, `BAD_EVON_RESPONSE`, `EMPTY_EVON_REPLY` | 502 | Reasoning service problem |
| `TIMBRE_UNREACHABLE`, `TTS_STREAM_ERROR`, `NO_AUDIO` | 502 | Text-to-speech problem |
| Gnani codes such as `INVALID_API_KEY` | 400 to 502 | Passed through from Gnani with a hint |

## 13. Security and privacy

- **Keys.** Keep every key in `.env` or in the hosting dashboard. Never put a key in `main.py` or `index.html`, never commit `.env`, and never paste a key into a chat, a screenshot or a video. If a key is exposed, delete it in its dashboard and create a new one.
- **No login.** Anyone with the link can use the app. The built-in limits cap the damage but live in memory and reset on restart, so check your usage dashboards after sharing publicly. Set `CORS_ORIGINS` to your real origin for production use.
- **Where your data goes.** Your audio is sent to Gnani (Prisma). Your transcript, code and error log are sent to the reasoning provider (Groq, serving `gpt-oss-120b`). The text of the answer is sent to Gnani (Timbre). Do not paste secrets, proprietary code, or real personal data. Use synthetic data in demos, as the challenge rules require.
- **What the server stores.** Nothing is saved to disk or a database. Audio and code are held in memory only while a request runs. Logs contain request ids, timings and error codes, plus the first 200 characters of a model reply when it could not be parsed.
- **Review the answers.** Suggested fixes come from a language model and from speech recognition that can mishear. Treat them as a second opinion and check the line yourself.

## 14. Status, limits and known issues

**Confirmed on the live deployment:** speech in (Prisma v2.5) and speech out (Timbre v2.5) in the Hinglish profile with the three bundled bugs, deployment on Render, and the reasoning model configured as `openai/gpt-oss-120b`. The quality of its answers to free-form questions should be checked with the manual test plan in section 16 after any change to the prompt or the model.

**Implemented from Gnani's documentation, test each before relying on it:** Tanglish and Benglish through Prisma v3.0 `transliterate` and the v2.5 fallback, the Hindi profile, and replies in Tamil, Bengali and Hindi script. The quality of those replies depends on the reasoning model, and the Tamil, Bengali and Hindi example lines in the prompts and demo rules were not reviewed by a native speaker.

**Not built yet:** the VS Code extension itself (the WebSocket API for it exists), conversation memory across questions, streaming recognition while you speak, and an automated test suite.

**Limits:**

- Questions are limited to 30 seconds of audio (the page stops at 28).
- Each question is answered on its own; the model does not remember the previous one.
- The demo rules cover three bug patterns only and ignore the question.
- Speech recognition can mishear technical terms, and a wrong transcript gives a wrong answer. The boosting makes this less likely, not impossible.
- A thinking model can take a few seconds before the first word of the answer. The stage list shows where the time goes.
- Free hosting sleeps when idle, and the in-memory limits reset when it does.
- Gnani accounts may allow only one active request at a time, so two simultaneous visitors can briefly queue.
- The page needs a browser with MediaRecorder, Web Audio and `fetch` streaming support. Recent Chrome and Edge are the most tested.

## 15. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Page says it is not served by the Dhwani-Code backend | You opened `index.html` directly, used a static host (Vercel, GitHub Pages, Live Server), or another program holds the port. Run the backend and open its own address |
| Page says the server is starting or restarting | A deploy is in progress or a free Render service is waking. Wait a minute and refresh. If it persists, read the last lines of Render's Logs |
| "Site can't be reached" on localhost | The server is not running. Start `python main.py`, keep the terminal open, and read what it prints |
| `ModuleNotFoundError` on start | Run `pip install -r requirements.txt` in the same Python you start the server with |
| `GNANI_API_KEY is not set` | `.env` is missing, misnamed (for example `env.example` or `.env.txt`) or the server was not restarted |
| 401 or `INVALID_API_KEY` | Wrong key, or a stray space or quote around it |
| 402 or 403 from Gnani | Out of credits, or the key lacks access to that service or model |
| 429 `RATE_LIMITED` ("tries for this hour") | The built-in visitor limit. Raise `RATE_LIMIT_PER_HOUR`, or open the site with `?owner=<OWNER_KEY>` |
| 429 from Gnani | The account allows one active request at a time. Wait and retry |
| "No speech was detected" | Recording too short or quiet. Check the level meter moves while you speak |
| Microphone blocked | Allow it in the browser's address bar. It works only on localhost or https |
| Header shows amber "Demo rules" | `EVON_BASE_URL` is not set on the running service, or the deploy has not finished |
| The same canned answer to every question | You are on the demo rules (no reasoning model connected), or an old version of the files is deployed |
| `EVON_HTTP_404` or a "model not found" message | `EVON_MODEL` is not a name in the provider's list. Copy it exactly |
| `EVON_HTTP_401` | The reasoning provider's key is wrong |
| `EMPTY_EVON_REPLY`, or answers cut off | A thinking model used its token budget. Set `EVON_MAX_TOKENS=2000`, or choose a non-thinking model |
| Transcript has the wrong technical words | Raise `STT_BIAS_SCORE` to 1.5, or try `STT_MODEL=prisma-v3.0` |
| Tanglish or Benglish transcript is in native script | Prisma v3.0 refused the request and the backend fell back to v2.5. Check that your key can use v3.0 |
| Fix appears but no sound | The browser blocked audio until you interact. Tap the mic once, or use the replay button |
| Garbled or clicking audio | Try `TTS_SAMPLE_RATE=22050`, and report it if it persists |

## 16. Manual test plan

There is no automated test suite yet. After any change, run through this on the deployed or local site:

1. `/api/health` shows `gnani_key_configured: true` and the right `reasoning_label`.
2. Header dots are green for Prisma, the reasoning model and Timbre.
3. Hinglish, `vector_sum.cpp`: ask the example question. Expect line 8 highlighted, the `<` fix, and spoken output.
4. Ask an explanation question on the same file ("heap buffer overflow hota kya hai?"). The box should say *Answer*, no line should be highlighted, and the answer should be about the concept.
5. Repeat step 3 on `grades.py` (line 3) and `linked_list.cpp` (line 10).
6. Tanglish and Benglish: ask the example question. Check the transcript script and that the reply is spoken in the right language. Note whether the fallback to v2.5 happened (the transcript will be in native script).
7. **Your code** tab: paste a bug the app has never seen, with its error message, and ask about it.
8. Typed question: send one without using the microphone.
9. Replay button, and interrupting playback by tapping the microphone.
10. Limits: with a low `RATE_LIMIT_PER_HOUR`, check that the message appears and that `?owner=` bypasses it.

## 17. Project structure

```
main.py                 FastAPI app: settings, Prisma/Timbre/reasoning clients, pipeline, routes
index.html              The whole web UI: mock IDE, recorder, event-stream reader, audio player
requirements.txt        Python dependencies
.env.example            Every setting, documented. Copy to .env
settings-template.txt   Ready-to-fill values for Groq as the reasoning model
README.md               This document
LICENSE                 MIT
```

Inside `main.py`, in order: configuration and language profiles; errors; data models; text helpers (vocabulary building, prompt assembly, speech clean-up, audio validation); the Prisma client; the reasoning client and the demo rules; the Timbre client; the pipeline and rate limiter; the HTTP and WebSocket routes.

## 18. Roadmap

1. **VS Code extension** that captures diagnostics and the active editor automatically and talks to `/ws/debug`.
2. **Gnani Evon v3.3** as the reasoning model once it is hosted, which is a settings change.
3. **Conversation memory**, so follow-ups like "and what if the vector is empty?" work.
4. **Streaming recognition**, so the transcript appears while you are still speaking.
5. **More languages**: Prisma and Timbre cover ten Indian languages.
6. **Automated tests and CI** for the vocabulary builder, prompt, parsing and routes.

## 19. Challenge submission summary

| Field | Text |
|---|---|
| Title | Dhwani-Code: a voice debugger in Hinglish, Tanglish and Benglish |
| Problem | Developers in India think in code-mixed languages, but debugging tools expect typed English, so they leave the editor to search and translate every error |
| Who will use it | Students and developers who code and talk about code in Hinglish, Tanglish, Benglish or Hindi |
| Gnani technology | Prisma v2.5 (Hinglish, Hindi, with vocabulary boosting from the user's code), Prisma v3.0 (Tanglish and Benglish romanised transcripts, with automatic fallback to v2.5), Timbre v2.5 (streamed speech). Reasoning by openai/gpt-oss-120b on Groq; Gnani's Evon v3.3 is open-weights and not on the API platform, and the reasoning step is built to switch to it by configuration |
| Contribution | Solo project. Designed and built the backend, interface, prompts, language handling and deployment |
| Demo video | <https://youtu.be/i5sSHQD05Xw> |
| Live app | <https://dhwani-code-voice-debugging.onrender.com> |

## 20. Credits

- **Gnani AI**: Prisma (speech to text) and Timbre (text to speech), and the Great Indian AI Internship Challenge this was built for.
- **OpenAI** (the `gpt-oss-120b` open-weights model) and **Groq** (the API that serves it).
- **Render** for hosting, and **FastAPI**, **httpx** and **Uvicorn** for the backend.
- Fonts: Mukta and Spline Sans Mono from Google Fonts.
