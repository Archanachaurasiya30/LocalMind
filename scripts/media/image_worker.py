"""One-shot optional CUDA worker. Not imported by the sidecar; no weight code runs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

MODEL = "segmind/SSD-1B"
REVISION = "60987f37e94cd59c36b1cba832b9f97b57395a10"


def event(state, progress, message):
    print(json.dumps({"state": state, "progress": progress, "message": message}), flush=True)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024**2), b""):
            result.update(block)
    return result.hexdigest()


def install(root):
    # Optional dependencies exist only in the user-created image runtime.
    import requests
    from huggingface_hub import HfApi, hf_hub_url
    if shutil.disk_usage(root).free < 20 * 1024**3:
        raise ValueError("Image setup needs 20 GiB of free disk. Free space, then retry.")
    info = HfApi().model_info(MODEL, revision=REVISION, files_metadata=True)
    files = [f for f in info.siblings if f.rfilename.endswith((".json", ".txt", ".md", ".fp16.safetensors"))]
    total = sum(f.size or 0 for f in files)
    weights = [f for f in files if f.rfilename.endswith(".safetensors")]
    if len(weights) != 4 or not 4_000_000_000 < total < 5_000_000_000:
        raise ValueError("Pinned model inventory changed. Installation stopped.")
    completed, receipts = 0, []
    for file in files:
        relative = Path(file.rfilename)
        if relative.is_absolute() or ".." in relative.parts or "\\" in file.rfilename:
            raise ValueError("Invalid model file path.")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = getattr(file.lfs, "sha256", None)
        if file in weights and not expected:
            raise ValueError("Missing model checksum.")
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with requests.get(hf_hub_url(MODEL, file.rfilename, revision=REVISION), stream=True, timeout=60) as response:
                response.raise_for_status()
                count = 0
                with temporary.open("wb") as output:
                    for block in response.iter_content(1024 * 1024):
                        count += len(block)
                        if count > file.size:
                            raise ValueError("Model file exceeded its pinned size.")
                        output.write(block)
                        event("downloading", int(100 * (completed + count) / total),
                              f"Downloaded {(completed + count) / 1024**3:.2f} of {total / 1024**3:.2f} GiB.")
                if count != file.size or (expected and digest(temporary) != expected):
                    raise ValueError("Model download checksum or size did not match. Retry installation.")
                temporary.replace(target)
                receipts.append({"path": file.rfilename, "sha256": digest(target), "bytes": count})
                completed += count
        finally:
            temporary.unlink(missing_ok=True)
    receipt = root / "localmind-verified.json"
    receipt.write_text(json.dumps({"revision": REVISION, "files": receipts}), encoding="utf-8")


def generate(root, request):
    import torch
    from diffusers import StableDiffusionXLPipeline
    if not torch.cuda.is_available():
        raise ValueError("This image pilot needs an NVIDIA CUDA GPU. Continue using chat; no paid fallback was started.")
    free, _ = torch.cuda.mem_get_info()
    if free < 4.5 * 1024**3:
        raise ValueError("Image generation needs 4.5 GiB free GPU memory. Close competing GPU work or unload your chat model manually, then retry.")
    receipt = json.loads((root / "localmind-verified.json").read_text(encoding="utf-8"))
    if receipt.get("revision") != REVISION or not receipt.get("files"):
        raise ValueError("Install the pinned model with Media before generating.")
    event("generating", 0, "Verifying the optional model.")
    for file in receipt["files"]:
        path = (root / file["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != file["sha256"]:
            raise ValueError("Image model checksum changed. Reinstall before generating.")
    body = json.loads(request.read_text(encoding="utf-8"))
    event("generating", 0, "Loading the image model into its separate worker.")
    pipe = StableDiffusionXLPipeline.from_pretrained(root, torch_dtype=torch.float16,
        variant="fp16", use_safetensors=True, local_files_only=True, add_watermarker=False)
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_tiling()
    def progress(_pipe, step, _timestep, values):
        event("generating", int((step + 1) * 100 / 20), f"Rendering step {step + 1} of 20.")
        return values
    image = pipe(body["prompt"], negative_prompt="blurry, poor quality, distorted",
                 width=512, height=512, num_inference_steps=20, guidance_scale=9,
                 num_images_per_prompt=1, generator=torch.Generator("cpu").manual_seed(body["seed"]),
                 callback_on_step_end=progress).images[0]
    # PNG text metadata preserves provenance when exported alone.
    from PIL.PngImagePlugin import PngInfo
    metadata = PngInfo()
    for name, value in {"Generated": "LocalMind experimental image", "Prompt": body["prompt"],
                        "Model": MODEL, "Revision": REVISION, "License": "Apache-2.0 (model card)",
                        "Seed": str(body["seed"])}.items():
        metadata.add_text(name, value)
    image.save(request.parent / "output.png", pnginfo=metadata)


def main():
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_XET="1")
    action, location, request = sys.argv[1:]
    root = Path(location)
    root.mkdir(parents=True, exist_ok=True)
    try:
        if action == "install":
            (root / "localmind-verified.json").unlink(missing_ok=True)
            install(root)
        elif action == "generate":
            generate(root, Path(request))
        else:
            raise ValueError("Unknown image operation.")
    except Exception as error:
        # Dependency/provider errors can include paths or URLs; expose only owned errors.
        message = str(error) if type(error) is ValueError else "Image worker failed. Check the optional CUDA runtime, GPU memory and disk space."
        event("failed", 0, message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
