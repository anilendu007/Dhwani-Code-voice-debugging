import base64
import io
import json
import unittest
import wave
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

import main


def settings() -> main.Settings:
    return main.Settings(
        gemini_api_key="test-key",
        gemini_model="gemini-test",
        gemini_tts_model="gemini-tts-test",
        gemini_tts_voice="Kore",
        gemini_timeout=10,
        judge0_api_key="judge0-test-key",
        judge0_api_url=main.JUDGE0_DEFAULT_URL,
        max_upload_bytes=1024 * 1024,
        cors_origins=["*"],
        rate_limit_per_hour=8,
        daily_cap=200,
        owner_key="",
    )


def response_text(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 16)
    return output.getvalue()


class GeminiClientTests(unittest.IsolatedAsyncioTestCase):
    def test_health_and_config_report_gemini_without_exposing_key(self) -> None:
        with TestClient(main.app) as client:
            health = client.get("/api/health").json()
            config = client.get("/api/config").json()
            models_response = client.get("/api/models")

        self.assertIn("gemini_key_configured", health)
        self.assertEqual(config["analysis_model"], main.Settings.from_env().gemini_model)
        self.assertEqual(config["reasoning"], "gemini")
        self.assertEqual(models_response.status_code, 503)
        self.assertEqual(models_response.json()["error"]["code"], "NO_API_KEY")
        self.assertNotIn("api_key", json.dumps(health).lower())
        self.assertNotIn("api_key", json.dumps(config).lower())

    def test_model_resource_prefix_is_normalized(self) -> None:
        self.assertEqual(main._model_id(" models/gemini-3.8-flash "), "gemini-3.8-flash")

    async def test_model_list_uses_api_key_and_normalizes_resource_names(self) -> None:
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={
                "models": [{
                    "name": "models/gemini-3.8-flash",
                    "baseModelId": "gemini-3.8-flash",
                    "displayName": "Gemini Flash",
                    "supportedGenerationMethods": ["generateContent"],
                }],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            available = await main.GeminiClient(settings(), http).list_models()

        self.assertEqual(available[0]["id"], "gemini-3.8-flash")
        self.assertIn("generateContent", available[0]["methods"])
        self.assertEqual(requests[0].url.path, "/v1beta/models")
        self.assertEqual(requests[0].headers["x-goog-api-key"], "test-key")

    async def test_unavailable_generation_model_has_actionable_error(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            with self.assertRaises(main.PipelineError) as error:
                await client._post("models/gemini-test:generateContent", {}, "analysis")

        self.assertEqual(error.exception.code, "MODEL_NOT_AVAILABLE")
        self.assertIn("/api/models", error.exception.message)

    async def test_transient_model_overload_retries_and_succeeds(self) -> None:
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            with patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                result = await client._post("models/gemini-test:generateContent", {}, "analysis")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(requests), 2)
        sleep.assert_awaited_once_with(1)

    async def test_persistent_model_overload_returns_clear_retry_message(self) -> None:
        requests = []

        async def handler(_: httpx.Request) -> httpx.Response:
            requests.append(True)
            return httpx.Response(503, json={
                "error": {
                    "status": "UNAVAILABLE",
                    "message": "This model is currently experiencing high demand.",
                },
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            with patch.object(main.asyncio, "sleep", new_callable=AsyncMock):
                with self.assertRaises(main.PipelineError) as error:
                    await client._post("models/gemini-test:generateContent", {}, "analysis")

        self.assertEqual(len(requests), 3)
        self.assertEqual(error.exception.code, "GEMINI_TEMPORARILY_UNAVAILABLE")
        self.assertEqual(error.exception.status, 503)
        self.assertIn("high demand", error.exception.message)

    async def test_judge0_run_uses_supported_language_and_limits(self) -> None:
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": "42\n",
                "stderr": "",
                "compile_output": None,
                "exit_code": 0,
                "time": "0.01",
                "memory": 1024,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await main.Judge0Client(settings(), http).run(
                main.RunRequest(language="JavaScript", code="console.log(42)", stdin="input\n")
            )

        self.assertEqual(result["stdout"], "42\n")
        self.assertTrue(result["success"])
        self.assertEqual(requests[0].url.path, "/submissions")
        self.assertEqual(requests[0].url.params["wait"], "false")
        self.assertEqual(requests[0].headers["x-rapidapi-key"], "judge0-test-key")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["language_id"], main.JUDGE0_LANGUAGE_IDS["JavaScript"])
        self.assertEqual(payload["stdin"], "input\n")
        self.assertEqual(payload["wall_time_limit"], 5)

    async def test_judge0_polls_queued_submissions(self) -> None:
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(201, json={"token": "submission-token"})
            if len([item for item in requests if item.method == "GET"]) == 1:
                return httpx.Response(200, json={"status": {"id": 2, "description": "Processing"}})
            return httpx.Response(200, json={
                "status": {"id": 3, "description": "Accepted"},
                "stdout": "done",
                "exit_code": 0,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with patch.object(main.asyncio, "sleep", new_callable=AsyncMock):
                result = await main.Judge0Client(settings(), http).run(
                    main.RunRequest(language="Python", code="print('done')")
                )

        self.assertEqual(result["stdout"], "done")
        self.assertEqual([request.method for request in requests], ["POST", "GET", "GET"])
        self.assertIn("/submissions/submission-token", requests[1].url.path)

    async def test_judge0_requires_configuration_and_rejects_unknown_language(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected request"))) as http:
            client = main.Judge0Client(
                main.Settings(
                    gemini_api_key="test-key",
                    gemini_model="gemini-test",
                    gemini_tts_model="gemini-tts-test",
                    gemini_tts_voice="Kore",
                    gemini_timeout=10,
                    judge0_api_key="",
                    judge0_api_url=main.JUDGE0_DEFAULT_URL,
                    max_upload_bytes=1024,
                    cors_origins=["*"],
                    rate_limit_per_hour=8,
                    daily_cap=200,
                    owner_key="",
                ),
                http,
            )
            with self.assertRaises(main.PipelineError) as error:
                await client.run(main.RunRequest(language="Python", code="print(1)"))

        self.assertEqual(error.exception.code, "COMPILER_NOT_CONFIGURED")
        with self.assertRaises(ValueError):
            main.RunRequest(language="Ruby", code="puts 1")

    async def test_transcription_sends_audio_with_server_key(self) -> None:
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=response_text("why does total stay zero?"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            result = await client.transcribe(
                main.AudioClip(b"fake audio", "speech.wav", "audio/wav"),
                main.EditorContext(code="total = 0"),
                main.PROFILES["hinglish"],
            )

        self.assertEqual(result.text, "why does total stay zero?")
        self.assertEqual(requests[0].headers["x-goog-api-key"], "test-key")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["contents"][0]["parts"][1]["inlineData"]["mimeType"], "audio/wav")
        self.assertIn("total = 0", payload["contents"][0]["parts"][0]["text"])

    async def test_analysis_prompts_for_logic_and_rejects_out_of_range_line(self) -> None:
        requests = []
        fix = {
            "root_cause": "The accumulator is reset in the loop.",
            "line_to_fix": 99,
            "suggested_code": "total += value",
            "speech_script": "Line ninety nine is resetting the total.",
            "confidence": "high",
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=response_text(json.dumps(fix)))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            result = await client.analyze(
                "Why is the result wrong?",
                main.EditorContext(
                    file_name="sum.py",
                    language="Python",
                    code="def sum_values(values):\n    total = 0\n    return total",
                ),
                main.PROFILES["hinglish"],
            )

        payload = json.loads(requests[0].content)
        system_prompt = payload["systemInstruction"]["parts"][0]["text"].lower()
        self.assertIn("trace the relevant code path", system_prompt)
        self.assertIn("logical bugs", system_prompt)
        self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingLevel"], "high")
        self.assertIsNone(result.line_to_fix)
        self.assertEqual(result.suggested_code, "")
        self.assertEqual(result.confidence, "low")

    async def test_tts_extracts_pcm_and_sample_rate_from_wav(self) -> None:
        source_wav = wav_bytes()
        encoded = base64.b64encode(source_wav).decode("ascii")
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"output_audio": {"data": encoded}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = main.GeminiClient(settings(), http)
            pcm, sample_rate, result_wav = await client.speak("Hello", main.PROFILES["hinglish"])

        self.assertTrue(requests[0].url.path.endswith("/interactions"))
        self.assertEqual(requests[0].headers["x-goog-api-key"], "test-key")
        self.assertEqual(pcm, b"\x00\x00" * 16)
        self.assertEqual(sample_rate, 24000)
        self.assertEqual(result_wav, source_wav)

    async def test_missing_key_fails_before_network_request(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected request"))) as http:
            client = main.GeminiClient(
                main.Settings(
                    gemini_api_key="",
                    gemini_model="gemini-test",
                    gemini_tts_model="gemini-tts-test",
                    gemini_tts_voice="Kore",
                    gemini_timeout=10,
                    judge0_api_key="",
                    judge0_api_url=main.JUDGE0_DEFAULT_URL,
                    max_upload_bytes=1024,
                    cors_origins=["*"],
                    rate_limit_per_hour=8,
                    daily_cap=200,
                    owner_key="",
                ),
                http,
            )
            with self.assertRaises(main.PipelineError) as error:
                await client.transcribe(
                    main.AudioClip(b"audio", "speech.wav", "audio/wav"),
                    main.EditorContext(),
                    main.PROFILES["hinglish"],
                )
        self.assertEqual(error.exception.code, "NO_API_KEY")


if __name__ == "__main__":
    unittest.main()
