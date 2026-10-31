import asyncio
import base64
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import zlib

import httpx
from fastapi import FastAPI

from app import media, media_store as store, media_files
from app.api.routes.media import router, checked_file
from app.api.middleware.auth import verify_bearer_token
from app.inference_queue import current_queue, inference_slot


def png_bytes():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


SOURCE = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()


def response(body, code=200):
    return httpx.Response(code, json=body, request=httpx.Request("GET", "https://api.dev.runwayml.com/v1/tasks/test"))


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        env = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": self.folder.name, "LOCALMIND_IMAGE_PILOT": "0", "LOCALMIND_TOKEN": "media-test-token"})
        env.start()
        self.addCleanup(env.stop)
        self.jobs = media.MediaJobs()
        self.key = patch("app.media.key", return_value="private-test-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    async def asyncTearDown(self):
        await self.jobs.shutdown()

    def prepare(self, **changes):
        return media.prepare_video(**({"prompt": "Slow camera movement", "source": SOURCE, "duration": 5, "ratio": "1280:720"} | changes))

    async def drain(self, job):
        task = self.jobs.tasks.get(job["id"])
        if task:
            await task

    def test_prepare_is_local_and_preserves_exact_review(self):
        with patch("app.media.provider", AsyncMock()) as api:
            job = self.prepare()
            self.assertEqual(job["state"], "review")
            self.assertEqual(job["cost_cents"], 25)
            self.assertEqual((store.folder(job["id"]) / "source.png").read_bytes(), png_bytes())
            api.assert_not_called()
            self.assertNotIn("source_hash", job)

    def test_rejects_invalid_prompt_duration_ratio_and_image(self):
        for changes in [{"prompt": " "}, {"prompt": "😀" * 501}, {"duration": 30}, {"ratio": "4k"},
                        {"source": "https://example.com/image.png"}, {"source": SOURCE + "garbage"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        self.assertEqual(store.listing(), [])

    async def test_double_confirm_sends_one_identical_paid_request(self):
        job = self.prepare()
        task_id = str(uuid4())
        with patch("app.media.provider", AsyncMock(return_value=response({"id": task_id}))) as api, patch.object(self.jobs, "poll", AsyncMock()):
            await asyncio.gather(self.jobs.submit(job["id"]), self.jobs.submit(job["id"]))
            await self.drain(job)
            self.assertEqual(api.await_count, 1)
            payload = api.call_args.args[3]
            self.assertEqual(payload["promptImage"], SOURCE)
            self.assertEqual(payload["duration"], 5)
            self.assertEqual(store.get(job["id"])["provider_id"], task_id)

    async def test_changed_source_is_never_submitted(self):
        job = self.prepare()
        (store.folder(job["id"]) / "source.png").write_bytes(b"changed")
        with patch("app.media.provider", AsyncMock()) as api, self.assertRaises(ValueError):
            await self.jobs.submit(job["id"])
        api.assert_not_called()
        self.assertFalse(store.get(job["id"])["reserved"])

    async def test_timeout_after_acceptance_never_resubmits_or_resumes_without_id(self):
        job = self.prepare()
        with patch("app.media.provider", AsyncMock(side_effect=httpx.ReadTimeout("secret-provider-detail"))) as api:
            await self.jobs.submit(job["id"])
            await self.drain(job)
            await self.jobs.submit(job["id"])
            self.jobs.recover()
            with self.assertRaises(ValueError):
                self.jobs.resume(job["id"])
            self.assertEqual(api.await_count, 1)
        saved = store.get(job["id"])
        self.assertEqual(saved["state"], "unknown")
        self.assertTrue(saved["reserved"])
        self.assertNotIn("secret", saved["message"])

    async def test_server_error_or_malformed_acceptance_is_unknown(self):
        for result in [response({}, 500), response({"id": "not-a-uuid"}), response({})]:
            job = self.prepare()
            with patch("app.media.provider", AsyncMock(return_value=result)):
                await self.jobs.submit(job["id"])
                await self.drain(job)
            self.assertEqual(store.get(job["id"])["state"], "unknown")

    async def test_rejected_key_is_clear_and_releases_reservation(self):
        job = self.prepare()
        with patch("app.media.provider", AsyncMock(return_value=response({"error": "private-test-key"}, 401))):
            await self.jobs.submit(job["id"])
            await self.drain(job)
        saved = store.get(job["id"])
        self.assertEqual(saved["state"], "failed")
        self.assertFalse(saved["reserved"])
        self.assertIn("API key", saved["message"])
        self.assertNotIn("private-test-key", json.dumps(store.listing()))

    def test_budget_counts_unknown_submissions_and_is_transactional(self):
        old = self.prepare()
        store.update(old["id"], state="unknown", reserved=True, cost_cents=5000, submitted_at=store.now())
        job = self.prepare()
        with self.assertRaises(ValueError):
            store.claim(job["id"])
        self.assertEqual(store.get(job["id"])["state"], "review")

    def test_restart_marks_inflight_submission_unknown_without_post(self):
        job = self.prepare()
        store.claim(job["id"])
        with patch.object(self.jobs, "launch") as launch:
            self.jobs.recover()
        launch.assert_not_called()
        self.assertEqual(store.get(job["id"])["state"], "unknown")

    async def test_restart_polls_saved_id_and_saves_only_validated_output(self):
        job = self.prepare()
        provider_id = str(uuid4())
        store.update(job["id"], state="queued", provider_id=provider_id)
        async def save(_url, destination):
            destination.write_bytes(b"fixture")
        with patch("app.media.provider", AsyncMock(return_value=response({"status": "SUCCEEDED", "output": ["https://example.cloudfront.net/output.mp4"]}))) as api, patch("app.media.download", AsyncMock(side_effect=save)):
            self.jobs.recover()
            await self.drain(job)
        self.assertEqual(api.call_args.args[:2], ("GET", "tasks/" + provider_id))
        self.assertEqual(store.get(job["id"])["state"], "completed")
        self.assertTrue(store.get(job["id"])["output"])

    async def test_transient_poll_failure_retries_get_not_generation(self):
        job = self.prepare()
        store.update(job["id"], state="queued", provider_id=str(uuid4()))
        with patch("app.media.provider", AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), response({"status": "FAILED"})])) as api, patch("app.media.asyncio.sleep", AsyncMock()):
            await self.jobs.poll(job["id"])
        self.assertEqual([call.args[0] for call in api.call_args_list], ["GET", "GET"])
        self.assertEqual(store.get(job["id"])["state"], "failed")

    async def test_bad_download_pauses_without_false_success_or_rebilling(self):
        job = self.prepare()
        store.update(job["id"], state="queued", provider_id=str(uuid4()))
        with patch("app.media.provider", AsyncMock(return_value=response({"status": "SUCCEEDED", "output": ["https://cdn.example/output"]}))) as api, patch("app.media.download", AsyncMock(side_effect=ValueError("invalid output"))), patch("app.media.asyncio.sleep", AsyncMock()):
            await self.jobs.poll(job["id"])
        saved = store.get(job["id"])
        self.assertEqual(saved["state"], "paused")
        self.assertFalse(saved["output"])
        self.assertEqual(api.await_count, 3)

    async def test_dismissed_review_never_submits(self):
        job = self.prepare()
        await self.jobs.cancel(job["id"])
        with patch("app.media.provider", AsyncMock()) as api:
            await self.jobs.submit(job["id"])
        api.assert_not_called()
        self.assertEqual(store.get(job["id"])["state"], "cancelled")

    async def test_late_cancel_does_not_erase_completed_image(self):
        job = store.create("image", "Finished tree")
        store.update(job["id"], state="completed", output=True)
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        self.jobs.tasks[job["id"]] = task
        await self.jobs.cancel(job["id"])
        self.assertEqual(store.get(job["id"])["state"], "completed")

    async def test_image_gate_blocks_install_and_model_launch(self):
        with patch("app.media.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            for install in (False, True):
                with self.assertRaises(ValueError):
                    self.jobs.start_image("A tree", install=install)
        spawn.assert_not_called()
        self.assertEqual(store.listing(), [])

    async def test_image_crash_releases_slot_and_chat_still_runs(self):
        script = self.root / "worker.py"
        script.write_text('import json,sys\nprint(json.dumps({"state":"failed","message":"Insufficient GPU memory."}),flush=True)\nsys.exit(1)\n')
        model = self.root / "image-model"
        model.mkdir()
        (model / "localmind-verified.json").write_text("{}")
        with patch.dict(os.environ, {"LOCALMIND_IMAGE_PILOT": "1", "LOCALMIND_IMAGE_PYTHON": sys.executable}), patch("app.media.worker_script", return_value=script):
            job = self.jobs.start_image("A tree")
            with self.assertRaises(ValueError):
                self.jobs.start_image("Another tree")
            await self.drain(job)
            self.assertEqual(store.get(job["id"])["state"], "failed")
            self.assertIn("GPU memory", store.get(job["id"])["message"])
            self.assertFalse(current_queue().active)
            async with inference_slot("foreground"):
                self.assertTrue(current_queue().active)
            # Exercise the real chat stream path after the worker failure, with
            # a deterministic Ollama transport so this test uses no live model.
            from app.api.routes.chat import ChatRequest, _stream_ollama
            class Response:
                def raise_for_status(self):
                    pass
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_):
                    pass
                async def aiter_lines(self):
                    yield json.dumps({"message": {"content": "Hello"}})
                    yield json.dumps({"done": True})
            class Client:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_):
                    pass
                def stream(self, *_, **__):
                    return Response()
            with patch("app.api.routes.chat.httpx.AsyncClient", return_value=Client()):
                frames = [json.loads(frame.removeprefix("data: ")) async for frame in _stream_ollama(
                    ChatRequest(message="hello", model="test", connector_context_enabled=False))]
            self.assertEqual(frames[-1]["type"], "done")
            self.assertTrue(any(frame.get("content") == "Hello" for frame in frames))

    async def test_cancel_queued_image_does_not_launch_child(self):
        model = self.root / "image-model"
        model.mkdir()
        (model / "localmind-verified.json").write_text("{}")
        with patch.dict(os.environ, {"LOCALMIND_IMAGE_PILOT": "1", "LOCALMIND_IMAGE_PYTHON": sys.executable}), patch("app.media.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            async with inference_slot("foreground"):
                job = self.jobs.start_image("A tree")
                await asyncio.sleep(0)
                await self.jobs.cancel(job["id"])
            spawn.assert_not_called()
        self.assertEqual(store.get(job["id"])["state"], "cancelled")
        self.assertFalse(current_queue().active)

    async def test_cancel_running_worker_waits_for_exit_before_chat_capacity(self):
        script = self.root / "slow_worker.py"
        script.write_text('import json,time\nprint(json.dumps({"state":"generating","progress":10}),flush=True)\ntime.sleep(120)\n')
        model = self.root / "image-model"
        model.mkdir()
        (model / "localmind-verified.json").write_text("{}")
        children = []
        original = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            if sys.platform == "win32":
                self.assertEqual(kwargs["creationflags"], 0x08000000)
            return child
        with patch.dict(os.environ, {"LOCALMIND_IMAGE_PILOT": "1", "LOCALMIND_IMAGE_PYTHON": sys.executable}), patch("app.media.worker_script", return_value=script), patch("app.media.asyncio.create_subprocess_exec", side_effect=spawn):
            job = self.jobs.start_image("A tree")
            async with asyncio.timeout(10):
                while store.get(job["id"])["state"] != "generating":
                    await asyncio.sleep(.02)
            await self.jobs.cancel(job["id"])
        self.assertIsNotNone(children[0].returncode)
        self.assertFalse(current_queue().active)
        self.assertEqual(store.get(job["id"])["state"], "cancelled")

    async def test_install_does_not_hold_chat_inference_slot(self):
        script = self.root / "installer.py"
        script.write_text('import json,time\nprint(json.dumps({"state":"downloading","progress":10}),flush=True)\ntime.sleep(120)\n')
        with patch.dict(os.environ, {"LOCALMIND_IMAGE_PILOT": "1", "LOCALMIND_IMAGE_PYTHON": sys.executable}), patch("app.media.worker_script", return_value=script):
            job = self.jobs.start_image(install=True)
            async with asyncio.timeout(10):
                while store.get(job["id"])["state"] != "downloading":
                    await asyncio.sleep(.02)
            self.assertFalse(current_queue().active)
            async with inference_slot("foreground"):
                self.assertTrue(current_queue().active)
            await self.jobs.cancel(job["id"])

    def test_restart_marks_local_work_failed_and_keeps_completed_jobs(self):
        job = store.create("image", "A tree")
        store.update(job["id"], state="generating")
        complete = store.create("image", "A finished tree")
        store.update(complete["id"], state="completed", output=True)
        self.jobs.recover()
        self.assertEqual(store.get(job["id"])["state"], "failed")
        self.assertEqual(store.get(complete["id"])["state"], "completed")

    def test_corrupt_and_oversized_png_rejected(self):
        self.assertEqual(media_files.png(png_bytes()), (1, 1))
        for data in [b"MZ executable", png_bytes()[:-4], png_bytes() + b"trailer", b"<html>error</html>"]:
            with self.assertRaises(ValueError):
                media_files.png(data)
        with self.assertRaises(ValueError):
            media_files.decode_image("data:image/png;base64," + "a" * 4_700_000)

    def test_mp4_container_validation_rejects_html_truncation_and_trailers(self):
        valid = b"\x00\x00\x00\x10ftypisom0000" + b"\x00\x00\x00\x0cmoov0000" + b"\x00\x00\x00\x0cmdat0000"
        media_files.video(valid)
        for data in [b"MZ executable", b"<html>no video</html>", valid[:-1], valid + b"junk"]:
            with self.assertRaises(ValueError):
                media_files.video(data)

    async def test_download_rejects_non_provider_urls_before_network(self):
        with patch("app.media_files.httpx.AsyncClient") as client:
            for url in ["http://cdn.cloudfront.net/video", "https://localhost/video", "https://cdn.cloudfront.net.evil.com/video", "https://user:pass@cdn.cloudfront.net/video", "file:///etc/passwd"]:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    await media_files.download(url, self.root / "output.mp4")
            client.assert_not_called()

    async def test_download_checks_size_type_and_body_before_publishing(self):
        original = httpx.AsyncClient
        valid = b"\x00\x00\x00\x10ftypisom0000" + b"\x00\x00\x00\x0cmoov0000" + b"\x00\x00\x00\x0cmdat0000"
        for content, headers, succeeds in [
            (b"<html>error</html>", {"content-type": "text/html"}, False),
            (b"fake mp4", {"content-type": "video/mp4"}, False),
            (valid, {"content-type": "video/mp4", "content-length": str(media_files.MAX_VIDEO + 1)}, False),
            (valid, {"content-type": "video/mp4"}, True),
        ]:
            target = self.root / (str(uuid4()) + ".mp4")
            transport = httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, content=content))
            def client(**kwargs):
                return original(transport=transport, **kwargs)
            with patch("app.media_files.httpx.AsyncClient", side_effect=client):
                if succeeds:
                    await media_files.download("https://example.cloudfront.net/output.mp4", target)
                    self.assertEqual(target.read_bytes(), valid)
                else:
                    with self.assertRaises(ValueError):
                        await media_files.download("https://example.cloudfront.net/output.mp4", target)
                    self.assertFalse(target.exists())
                    self.assertFalse(target.with_suffix(".part").exists())

    def test_export_requires_completed_verified_output_and_owned_id(self):
        job = self.prepare()
        for job_id in [job["id"], "../secrets"]:
            with self.assertRaises(ValueError):
                checked_file(job_id)
        job = store.create("image", "Tree")
        store.update(job["id"], state="completed", output=True)
        (store.folder(job["id"]) / "output.png").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            checked_file(job["id"])

    async def test_routes_are_authenticated_and_reject_unknown_fields(self):
        app = FastAPI()
        app.middleware("http")(verify_bearer_token)
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.get("/media/status")).status_code, 401)
            body = {"prompt": "test", "source": SOURCE, "duration": 5, "ratio": "960:960", "url": "https://evil.com"}
            result = await client.post("/media/prepare", json=body, headers={"Authorization": "Bearer media-test-token"})
            self.assertEqual(result.status_code, 422)

    def test_key_only_uses_native_credential_boundary(self):
        with patch("app.media.CredentialStore") as credential:
            credential.return_value.read.return_value = {"token": "test-secret"}
            media.set_key("test-secret")
            credential.return_value.write.assert_called_once_with("runway-token", {"token": "test-secret"})
            self.assertEqual(media.credential_status(), "saved")
            media.set_key("")
            credential.return_value.delete.assert_called_once_with("runway-token")
        self.assertFalse((self.root / "media.db").exists())


if __name__ == "__main__":
    unittest.main()
