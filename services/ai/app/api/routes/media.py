import base64
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import media
from app import media_store as store
from app.credentials import CredentialUnavailable
from app.media_files import png, video, MAX_IMAGE, MAX_VIDEO

router = APIRouter(prefix="/media", tags=["media"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Job(Strict):
    job_id: str = Field(min_length=36, max_length=36)


class Key(Strict):
    token: str = Field(max_length=1000)


class Image(Strict):
    prompt: str = Field(min_length=1, max_length=1000)


class Review(Image):
    source: str = Field(max_length=4_700_000)
    duration: Literal[5, 10]
    ratio: Literal["1280:720", "720:1280", "960:960"]


class CloudImage(Image):
    model: Literal['gpt-image-2', 'gpt-image-1.5', 'gpt-image-1-mini']
    allowance_cents: int = Field(ge=1, le=500)
    reference_id: str | None = Field(default=None, max_length=36)


@router.post('/cloud-image-prepare')
async def cloud_image_prepare(body: CloudImage):
    from app.cloud_images import prepare
    return await execute(lambda: prepare(**body.model_dump()))


@router.post('/cloud-image-submit')
async def cloud_image_submit(body: Job):
    from app.cloud_images import submit
    return await execute(lambda: submit(media.jobs(), store.identifier(body.job_id)))


async def execute(work):
    try:
        result = work()
        import inspect
        return await result if inspect.isawaitable(result) else result
    except CredentialUnavailable:
        raise HTTPException(400, "Unlock your operating system credential store and retry. No key was stored in a file.") from None
    except (ValueError, OSError) as error:
        detail = str(error) if type(error) is ValueError else "The local media file could not be read or saved."
        raise HTTPException(400, detail) from None


@router.get("/status")
async def status():
    return await execute(media.status)


@router.post("/credential")
async def credential(body: Key):
    return await execute(lambda: media.set_key(body.token))


@router.post("/prepare")
async def prepare(body: Review):
    return await execute(lambda: media.prepare_video(body.prompt, body.source, body.duration, body.ratio))


@router.post("/submit")
async def submit(body: Job):
    return await execute(lambda: media.jobs().submit(store.identifier(body.job_id)))


@router.post("/resume")
async def resume(body: Job):
    return await execute(lambda: media.jobs().resume(store.identifier(body.job_id)))


@router.post("/cancel")
async def cancel(body: Job):
    return await execute(lambda: media.jobs().cancel(store.identifier(body.job_id)))


@router.post("/image")
async def image(body: Image):
    return await execute(lambda: media.jobs().start_image(body.prompt))


@router.post("/install")
async def install(body: Strict):
    return await execute(lambda: media.jobs().start_image(install=True))


def checked_file(job_id, source=False):
    job = store.get(job_id)
    if source:
        if job["kind"] != "video":
            raise ValueError("This job has no source image.")
        path, mime, limit = store.folder(job_id) / "source.png", "image/png", MAX_IMAGE
    else:
        if not job["output"] or job["state"] != "completed":
            raise ValueError("This job has no verified output to export.")
        is_video = job["kind"] == "video"
        path = store.folder(job_id) / ("output.mp4" if is_video else "output.png")
        mime, limit = ("video/mp4", MAX_VIDEO) if is_video else ("image/png", MAX_IMAGE)
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("Invalid media file.")
    data = path.read_bytes()
    (video if mime == "video/mp4" else png)(data)
    return path, mime, data


@router.post("/preview")
async def preview(body: Job):
    def read():
        job = store.get(body.job_id)
        _, mime, data = checked_file(body.job_id, source=job["kind"] == "video")
        return {"image": "data:" + mime + ";base64," + base64.b64encode(data).decode()}
    return await execute(read)


@router.get("/file/{job_id}")
async def file(job_id: UUID):
    def read():
        path, mime, _ = checked_file(str(job_id))
        return FileResponse(path, media_type=mime, filename="localmind-" + str(job_id) + path.suffix,
                            headers={"X-Content-Type-Options": "nosniff"})
    return await execute(read)
