"""Optional local image work and explicitly reviewed Runway video operations."""
import asyncio
import base64
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID

import httpx

from app import media_store as store
from app.credentials import CredentialStore, CredentialUnavailable
from app.inference_queue import inference_slot
from app.media_files import decode_image, download, png, MAX_IMAGE
from app.storage import data_path

MODEL = "segmind/SSD-1B"
REVISION = "60987f37e94cd59c36b1cba832b9f97b57395a10"
PRICING_DATE = "2026-10-31"
ACTIVE_VIDEO = {"queued", "generating", "downloading", "paused"}
UNKNOWN = "Runway may have accepted this request. Check your Runway tasks and billing. LocalMind will not resubmit it."


def image_enabled():
    return os.environ.get("LOCALMIND_IMAGE_PILOT") == "1"


def runtime():
    path = Path(os.environ.get("LOCALMIND_IMAGE_PYTHON", ""))
    return str(path) if path.is_absolute() and path.is_file() else None


def worker_script():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "media-worker" / "image_worker.py"
    return Path(__file__).resolve().parents[3] / "scripts" / "media" / "image_worker.py"


def image_status():
    enabled = image_enabled()
    ready = enabled and bool(runtime())
    installed = (data_path("image-model") / "localmind-verified.json").is_file()
    return {"enabled": enabled, "runtime_ready": ready, "installed": installed,
            "download_bytes": 4_465_653_694, "model": MODEL, "revision": REVISION,
            "reason": ("Experimental: text, anatomy and composition did not pass quality review."
                       if ready else "Image generation is experimental and disabled. Set up the optional CUDA runtime and enable the image pilot to test it. No cloud fallback."),
            "requirements": "NVIDIA CUDA GPU with 4.5 GiB free VRAM; 20 GiB free disk. Other hardware is not supported by this pilot."}


def credential_status():
    try:
        value = CredentialStore().read("runway-token")
        return "saved" if value and value.get("token") else "missing"
    except CredentialUnavailable:
        return "locked"


def key():
    value = CredentialStore().read("runway-token")
    if not value or not value.get("token"):
        raise ValueError("Add a Runway API key in Media before submitting or resuming video.")
    return value["token"]


def set_key(token):
    if not token:
        CredentialStore().delete("runway-token")
    elif not token.isascii() or any(char.isspace() for char in token) or len(token) > 1000:
        raise ValueError("The API key has invalid characters or is too long.")
    else:
        CredentialStore().write("runway-token", {"token": token})
    return {"credential": credential_status()}


def public(job):
    return {name: value for name, value in job.items() if name not in {"source_hash"}}


def status():
    all_jobs = store.listing()
    month = store.now()[:7]
    return {"images": image_status(), "credential": credential_status(),
            "jobs": [public(job) for job in all_jobs[:100]],
            "reserved_cents": sum(j["cost_cents"] for j in all_jobs if j["reserved"]
                                  and j.get("submitted_at", "").startswith(month)),
            "limit_cents": 5000, "pricing_date": PRICING_DATE}


def prompt_text(prompt):
    prompt = prompt.strip()
    if not prompt or len(prompt.encode("utf-16-le")) // 2 > 1000:
        raise ValueError("Enter a prompt of up to 1000 characters (UTF-16 units).")
    return prompt


def prepare_video(prompt, source, duration, ratio):
    prompt = prompt_text(prompt)
    if duration not in (5, 10) or ratio not in ("1280:720", "720:1280", "960:960"):
        raise ValueError("Choose a supported duration and aspect ratio.")
    data = decode_image(source)
    job = store.create("video", prompt, model="gen4_turbo", location="Runway cloud",
                       license="Runway terms of use", duration=duration, ratio=ratio,
                       pricing_date=PRICING_DATE, source_hash=hashlib.sha256(data).hexdigest())
    try:
        (store.folder(job["id"]) / "source.png").write_bytes(data)
        return public(store.update(job["id"], cost_cents=duration * 5))
    except OSError:
        store.update(job["id"], state="failed", message="Could not save the reviewed image locally.")
        raise ValueError("Could not save the reviewed image locally.") from None


async def provider(method, path, token, body=None):
    # POST retries are deliberately absent, including transport retries.
    async with httpx.AsyncClient(timeout=45, follow_redirects=False, trust_env=False) as client:
        response = await client.request(method, "https://api.dev.runwayml.com/v1/" + path,
            headers={"Authorization": "Bearer " + token, "X-Runway-Version": "2024-11-06"},
            json=body)
    return response


class MediaJobs:
    def __init__(self):
        self.tasks = {}
        self.image_job = None
        self.polling = set()

    def launch(self, job_id, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks[job_id] = task
        def finished(done):
            if self.tasks.get(job_id) is done:
                self.tasks.pop(job_id, None)
            # Exceptions are reflected in persisted job state; consume on shutdown.
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def recover(self):
        for job in store.listing():
            if job["kind"] in ("image", "image-install") and job["state"] in {"queued", "generating", "downloading"}:
                store.update(job["id"], state="failed", message="Local image work stopped when LocalMind closed. You can start a new job.")
            elif job["kind"] == "video":
                if job["state"] == "submitting":
                    store.update(job["id"], state="unknown", message=UNKNOWN)
                elif job["provider_id"] and job["state"] in ACTIVE_VIDEO:
                    self.launch(job["id"], self.poll(job["id"]))

    async def submit(self, job_id):
        original = store.get(job_id)
        if original["kind"] != "video":
            raise ValueError("Only reviewed video jobs can be submitted.")
        token = key()  # Resolve credentials and validate input before claiming a paid request.
        data = (store.folder(job_id) / "source.png").read_bytes()
        if hashlib.sha256(data).hexdigest() != original["source_hash"]:
            raise ValueError("The reviewed image changed. Prepare a new review.")
        job, claimed = store.claim(job_id)
        if not claimed:
            return public(job)
        self.launch(job_id, self.send(job_id, token, data))
        return public(job)

    async def send(self, job_id, token, data):
        job = store.get(job_id)
        try:
            response = await provider("POST", "image_to_video", token, {
                "model": "gen4_turbo", "promptText": job["prompt"],
                "promptImage": "data:image/png;base64," + base64.b64encode(data).decode(),
                "duration": job["duration"], "ratio": job["ratio"]})
            if response.status_code in (400, 401, 403, 404, 413, 422, 429):
                reason = ("Runway rejected the API key. Replace it before preparing another job."
                          if response.status_code in (401, 403) else
                          "Runway rejected the request. Check account credits, input limits and content rules before creating a new review.")
                store.update(job_id, state="failed", reserved=False, message=reason)
                return
            response.raise_for_status()
            provider_id = str(UUID(response.json()["id"]))
            store.update(job_id, provider_id=provider_id, state="queued", message="Accepted by Runway.")
        except asyncio.CancelledError:
            store.update(job_id, state="unknown", message=UNKNOWN)
            raise
        except Exception:
            store.update(job_id, state="unknown", message=UNKNOWN)
            return
        await self.poll(job_id)

    async def poll(self, job_id):
        if job_id in self.polling:
            return
        self.polling.add(job_id)
        try:
            deadline = time.monotonic() + 15 * 60
            failures = 0
            while time.monotonic() < deadline:
                job = store.get(job_id)
                if not job["provider_id"] or job["state"] not in ACTIVE_VIDEO:
                    return
                try:
                    response = await provider("GET", "tasks/" + str(UUID(job["provider_id"])), key())
                    if response.status_code in (401, 403, 404):
                        store.update(job_id, state="paused", message="Could not access this Runway task. Check the key and task in Runway, then resume status checks. No new generation will be sent.")
                        return
                    response.raise_for_status()
                    result = response.json()
                    if result.get("id", job["provider_id"]) != job["provider_id"]:
                        raise ValueError("Unexpected provider task identity")
                    phase = result["status"]
                    if phase == "SUCCEEDED":
                        store.update(job_id, state="downloading", message="Validating and saving the generated video.")
                        outputs = result.get("output")
                        if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], str):
                            raise ValueError("Missing output")
                        await download(outputs[0], store.folder(job_id) / "output.mp4")
                        store.update(job_id, state="completed", progress=100, output=True, message="Generated video saved on this device.")
                        return
                    if phase in ("FAILED", "CANCELLED"):
                        store.update(job_id, state="failed" if phase == "FAILED" else "cancelled",
                                     message="Runway ended this task without a video. Check Runway for details and billing.")
                        return
                    if phase not in ("PENDING", "THROTTLED", "RUNNING"):
                        raise ValueError("Unknown provider state")
                    progress = result.get("progress", 0)
                    progress = min(99, max(0, int(float(progress) * 100)))
                    store.update(job_id, state="generating" if phase == "RUNNING" else "queued",
                                 progress=progress, message="Runway is generating." if phase == "RUNNING" else "Waiting for Runway capacity.")
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1
                    if failures >= 3:
                        store.update(job_id, state="paused", message="Status or download could not be verified. Resume checks for the same task; no new generation will be sent.")
                        return
                await asyncio.sleep(5 * max(1, failures))
            store.update(job_id, state="paused", message="Status checks timed out. Resume this existing task when ready; it will not be resubmitted.")
        finally:
            self.polling.discard(job_id)

    def resume(self, job_id):
        job = store.get(job_id)
        if job["kind"] != "video" or not job["provider_id"] or job["state"] != "paused":
            raise ValueError("Only a paused task with a saved Runway ID can resume.")
        key()
        if job_id not in self.tasks:
            store.update(job_id, state="queued", message="Resuming the existing Runway task.")
            self.launch(job_id, self.poll(job_id))
        return public(store.get(job_id))

    def start_image(self, prompt="", install=False):
        if not image_enabled() or not runtime():
            raise ValueError(image_status()["reason"])
        if self.image_job and self.image_job in self.tasks:
            raise ValueError("One image operation is already active. Wait or cancel it first.")
        if not install and not image_status()["installed"]:
            raise ValueError("Download the optional image model before generating.")
        job = store.create("image-install" if install else "image", "Install SSD-1B" if install else prompt_text(prompt),
                           model=MODEL, revision=REVISION, license="Apache-2.0 (model card)",
                           location="This device", seed=42)
        store.update(job["id"], state="queued", message="Waiting for local capacity.")
        self.image_job = job["id"]
        self.launch(job["id"], self.run_image(job["id"], install))
        return public(store.get(job["id"]))

    async def run_image(self, job_id, install):
        child = None
        try:
            async with AsyncExitStack() as capacity:
                # Downloading weights does not use the GPU and must not queue chat behind it.
                if not install:
                    await capacity.enter_async_context(inference_slot("background"))
                if not image_enabled():
                    raise ValueError("Image pilot is disabled.")
                job = store.get(job_id)
                request = store.folder(job_id) / "request.json"
                request.write_text(json.dumps({"prompt": job["prompt"], "seed": job["seed"]}), encoding="utf-8")
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(runtime(), "-I", str(worker_script()),
                    "install" if install else "generate", str(data_path("image-model")), str(request),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **options))
                try:
                    child = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # A cancellation during process creation must not leave an orphan worker.
                    child = await spawn
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                    raise
                async def consume():
                    async for line in child.stdout:
                        event = json.loads(line)
                        phase = event.get("state")
                        if phase not in ("downloading", "generating", "failed"):
                            raise ValueError("Invalid worker event")
                        store.update(job_id, state=phase, progress=min(99, max(0, int(event.get("progress", 0)))),
                                     message=str(event.get("message", ""))[:300])
                    if await child.wait():
                        raise ValueError("Image worker stopped")
                try:
                    await asyncio.wait_for(consume(), timeout=3600 if install else 600)
                finally:
                    # Keep the shared GPU slot until the worker has actually exited.
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                output = store.folder(job_id) / "output.png"
                if not install:
                    if output.stat().st_size > MAX_IMAGE:
                        raise ValueError("Image too large")
                    png(output.read_bytes())
                store.update(job_id, state="completed", progress=100, output=not install,
                             message="Optional image model installed." if install else "Generated image. Experimental quality; inspect before using.")
        except asyncio.CancelledError:
            store.update(job_id, state="cancelled", message="Image operation cancelled. GPU resources released when the worker exits.")
            raise
        except Exception:
            current = store.get(job_id)
            store.update(job_id, state="failed", message=current["message"] if current["state"] == "failed" else
                         "Image worker failed or timed out. Check CUDA memory, optional runtime and free disk, then retry. Text chat remains available.")
        finally:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            (store.folder(job_id) / "request.json").unlink(missing_ok=True)

    async def cancel(self, job_id):
        job = store.get(job_id)
        if job["kind"] == "video":
            if job["state"] != "review":
                raise ValueError("Submitted video tasks cannot be cancelled here. Check Runway for cancellation and billing.")
            store.update(job_id, state="cancelled", message="Review dismissed. Nothing was sent to Runway.")
        else:
            task = self.tasks.get(job_id)
            if task and not task.done():
                if not task.cancelling():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                store.update(job_id, state="cancelled", message="Image operation cancelled and worker stopped.")
        return public(store.get(job_id))

    async def shutdown(self):
        active = list(self.tasks.values())
        for task in active:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


_jobs = None


def jobs():
    global _jobs
    if _jobs is None:
        _jobs = MediaJobs()
    return _jobs
