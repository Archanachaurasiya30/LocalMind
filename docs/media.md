# Media studio (source beta)

Open **Media** in the sidebar for optional local image jobs and reviewed Runway image-to-video jobs. This feature is newer than the published 0.7.1 installer. Opening the page never starts a download, generation or paid submission.

## Image API in the current source build

The **Image API** section uses the OpenAI key saved in Settings. Prepare a prompt,
model and reservation, then review the exact request before sending it once.
Local-only mode blocks preparation and submission. Opening Media does not send
a request.

The reservation is user-supplied. It is not a price quote or a provider billing
cap. Both LocalMind's cloud budget and its media budget must permit the request.
Actual charges cannot be calculated reliably from this adapter's response, so
the reservation remains in the monthly usage ledger. There is no billing
reconciliation interface yet. Check the
provider's billing page before sending another request after an uncertain result.

Successful image bytes are validated and saved locally. The gallery keeps the
prompt, model, dimensions, timestamp and available provider request identifier.
You can zoom, save the image, attach it to chat or select it as a reference for
an edit. Editing creates a separate job and preserves the original. Once an
attached image is saved in a chat, **Open image workspace** shows it beside the
conversation.

Stopping a request stops the local wait. It does not promise a provider-side
cancellation or refund. Interrupted and uncertain requests are never submitted
again automatically. Provider rejection releases the reservation when the
response establishes that the request was rejected.

The adapter has simulated request/response tests, including multipart edits,
rejection, cancellation and restart recovery. A live paid acceptance run is still
required before advertising provider compatibility as verified.

## Local image pilot

The image pilot is disabled by default. The earlier SSD-1B trial passed the hardware limits but failed text, anatomy and composition quality review. Adding the worker does **not** change that result. Outputs remain experimental; there is no automatic cloud fallback.

The reference configuration is `segmind/SSD-1B`, pinned to revision `60987f37e94cd59c36b1cba832b9f97b57395a10`, FP16 safetensors, 512 × 512, 20 steps, guidance 9 and seed 42. The model card declares Apache-2.0. Review the [pinned model card](https://huggingface.co/segmind/SSD-1B/tree/60987f37e94cd59c36b1cba832b9f97b57395a10) and upstream notices before downloading or distributing results. PNG exports retain prompt, model, revision, license and seed metadata.

Requirements: NVIDIA CUDA GPU, at least 4.5 GiB free VRAM after CUDA initialization and 20 GiB free disk for setup. Other hardware is unsupported in this pilot. The app does not unload Ollama automatically; a loaded chat model may leave insufficient memory. There is no CPU or paid fallback.

Create a separate Python 3.11 image environment outside the repository and install the research dependencies into it. Example for Windows PowerShell, from the repository root:

```powershell
$imageRuntime = Join-Path $env:LOCALAPPDATA 'LocalMind\image-runtime'
py -3.11 -m venv $imageRuntime
& "$imageRuntime\Scripts\python.exe" -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu126
& "$imageRuntime\Scripts\python.exe" -m pip install -r scripts/evaluation/image-requirements.txt
$env:LOCALMIND_IMAGE_PYTHON = "$imageRuntime\Scripts\python.exe"
$env:LOCALMIND_IMAGE_PILOT = '1'
pnpm --filter desktop tauri dev
```

Use the same variables with an absolute interpreter path on other supported CUDA hosts. Python/CUDA setup is manual; Media does not install an interpreter or change drivers. Environment variables must be present when the sidecar starts.

In Media, review the model card and explicitly approve the approximately 4.16 GiB weight download. Progress reports real downloaded bytes. Files are stored in the current profile's `image-model` directory, independently of the desktop installer. Pinned inventory, byte counts and SHA-256 checks are validated; only configuration, tokenizer, notices and safetensors files are downloaded. No repository Python code is loaded. Generation verifies the saved file checksums and works offline.

One image operation runs per profile. Weight downloads leave chat available. Generation waits for the shared inference slot; queued foreground chat takes priority. The separate worker exits after each image, releasing CUDA allocations. Cancel kills and waits for the worker before releasing the slot. A crash, timeout or out-of-memory event marks the job failed, without restarting it or changing chat settings. Workers use no console window on Windows.

## Runway video

The first adapter uses **Gen-4 Turbo image-to-video**, API version `2024-11-06`. LocalMind deliberately supports only 5 or 10 seconds, PNG source images below 3.5 MB, prompts up to 1000 UTF-16 code units, and landscape `1280:720`, portrait `720:1280` or square `960:960`. These are a conservative subset of provider capabilities. Convert JPEG/WebP sources to PNG before selecting them. LocalMind bounds PNG dimensions to 4096 × 4096.

Pricing checked **October 31, 2026**: 5 credits per generated second; $0.01 per credit. The displayed estimate is $0.25 for 5 seconds or $0.50 for 10 seconds, before tax. Final charges are controlled by Runway and may change. Sources: [official pricing](https://docs.dev.runwayml.com/guides/pricing/), [API reference](https://docs.dev.runwayml.com/api/), [official request schema](https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/image_to_video_create_params.py), [terms](https://runway.com/terms-of-use) and [privacy policy](https://runway.com/privacy-policy). The terms page was dated October 23, 2026 when reviewed. Users must have rights to supplied content and review the provider's terms; this integration does not grant additional content rights or include a provider subscription.

1. Add your own Runway API key under **Runway connection**. LocalMind uses its existing profile-scoped OS credential store. There is no plaintext fallback, key echo, browser storage or key stored in the jobs database. A saved key has not necessarily been accepted by Runway; rejection is reported on submission.
2. Select an image, describe the motion, and choose duration/format.
3. **Review video request** saves an exact local copy. Inspect the image, prompt, destination and estimate. Approve the upload and charge, then choose **Send to Runway**. Editing the original form cannot change the saved review. Dismissing the review sends nothing.
4. Follow the job in **Your jobs**, then **Save video** to choose a destination. The original source frame remains local alongside its receipt. Submitted tasks cannot be cancelled from LocalMind in this pilot; use Runway and check its billing policy.

The local monthly allowance is $50 per profile, tracked in integer cents. Submission reserves the estimate before contacting Runway. Unknown outcomes and provider failures retain their reservation conservatively; definitive HTTP rejections release it. This is not an account-wide spending cap or the provider's balance. It does not include other apps, taxes or later price changes. Check Runway's own billing controls as well.

## Recovery and file boundaries

All operations use opaque local IDs and a shared SQLite journal in the private desktop profile. Local/cloud labels, prompt, model, state, progress and provenance survive restarts. State transitions distinguish review, submitting, queued, generating, downloading, completed, failed, cancelled, paused and unknown.

The app commits a submission claim **before** a paid POST, and persists the returned provider ID **before** polling. Repeat confirmation does not submit again. There are no HTTP POST retries and no assumed provider idempotency guarantee.

If the POST times out, returns an ambiguous error or is interrupted, the job becomes **unknown**. Check Runway tasks and billing manually before preparing another job. LocalMind cannot safely infer the missing provider ID and never resubmits that operation. Known tasks resume GET status checks after restart. Temporary polling or download failures pause after three attempts; **Resume status checks** only queries the saved task ID. Polling is bounded to 15 minutes per session; it does not cancel provider work.

Video downloads require HTTPS from recognized Runway/CDN hosts, no redirects, `video/mp4`, at most 100 MiB and a structurally valid MP4 container. Invalid content never becomes a completed output. Provider URLs are not rendered in the webview or accepted from user input. PNG input/output signatures, chunks, dimensions and checksums are checked. These checks establish container structure, not visual quality or full codec correctness. Files are stored as inert data and never executed.

Native export is restricted to verified completed jobs. It asks for a PNG/MP4 destination, validates the local file again, stages a temporary file beside the selected destination and renames after writing. Cancelling the dialog writes nothing. No local file path or provider API key is exposed to the frontend.

## Verification and remaining acceptance

Deterministic backend and UI fixtures exercise duplicate confirmation, timeout after acceptance, restart recovery, malformed provider responses, transient polling, budget reservations, local worker crash/cancellation, credential storage and export states. The worker source ships as data; torch, diffusers and model weights are excluded from release requirements.

Paid Runway generation and native export dialogs have not been exercised in this batch. A real CUDA generation through the new worker is also pending; the earlier benchmark is separate evidence. The next media acceptance stories should run those trials with explicit budget and GPU availability, inspect output quality and record results. Do not present fixture success as a live provider or visual-quality pass.
