"""Profile-owned image drafts. Image bytes and message links share one transaction store."""

import base64
import binascii
import json
from pathlib import Path
from contextlib import closing
from io import BytesIO
import time
from uuid import UUID, uuid4
import warnings
from threading import BoundedSemaphore

from app.db.conversations import _get_conn

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_PROFILE_BYTES = 512 * 1024 * 1024
MAX_IMAGES = 4
DRAFT_TTL = 48 * 3600
_DECODERS = BoundedSemaphore(2)


def identifier(value: str) -> str:
    try:
        result = str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Invalid attachment owner or identifier.") from None
    if result != value:
        raise ValueError("Invalid attachment owner or identifier.")
    return result


def normalize_image(encoded: str) -> tuple[bytes, str, int, int]:
    # Keep concurrent uploads from multiplying the decoded pixel-memory limit.
    if not _DECODERS.acquire(timeout=5):
        raise ValueError("Other images are still being processed. Try again shortly.")
    try:
        return _decode_image(encoded)
    finally:
        _DECODERS.release()


def _decode_image(encoded: str) -> tuple[bytes, str, int, int]:
    # Pillow loads only these formats; SVG, animated files and huge decompressed
    # images never enter the renderer or provider payload.
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
        raise ValueError("Images must be 8 MiB or smaller.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The image data is invalid.") from None
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Images must be between 1 byte and 8 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=["PNG", "JPEG", "WEBP"]) as original:
                if original.width * original.height > MAX_PIXELS or getattr(original, "n_frames", 1) != 1:
                    raise ValueError("Use a still image with no more than 16 million pixels.")
                original.load()
                oriented = ImageOps.exif_transpose(original)
                # Recreate pixels so EXIF, comments and profile metadata cannot
                # survive re-encoding. Keep screenshot text lossless.
                mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
                converted = oriented.convert(mode)
                clean = Image.frombytes(mode, converted.size, converted.tobytes())
                output = BytesIO()
                clean.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_BYTES:
                    raise ValueError("The decoded image is too large. Crop it before attaching.")
                return data, "image/png", clean.width, clean.height
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Could not read this image. Use a valid PNG, JPEG or WebP file.") from None


def _metadata(row, *, preview=False) -> dict:
    result = {key: row[key] for key in ("id", "filename", "mime_type", "width", "height")}
    result["bytes"] = len(row["data"])
    if row["mime_type"] == "application/x-localmind-document":
        result.update(json.loads(row["data"]))
        return result
    if preview:
        result["data_url"] = f"data:{row['mime_type']};base64," + base64.b64encode(row["data"]).decode("ascii")
    return result


def _owner(row, conversation_id, draft_id):
    if row is None:
        raise ValueError("Attachment not found in this profile.")
    if conversation_id:
        identifier(conversation_id)
        if row["conversation_id"] != conversation_id:
            raise ValueError("This attachment belongs to another conversation.")
    elif not draft_id or row["draft_id"] != identifier(draft_id) or row["message_id"]:
        raise ValueError("This attachment belongs to another draft.")


def create(filename: str, data: str, draft_id: str, conversation_id: str | None = None, mode='vision') -> dict:
    identifier(draft_id)
    if conversation_id:
        identifier(conversation_id)
    if mode not in ('vision', 'ocr'):
        raise ValueError('Choose vision or OCR text-only mode.')
    suffix = '.ocr' if mode == 'ocr' else Path(filename).suffix.lower()
    if suffix in ('.pdf', '.txt', '.md', '.csv', '.ocr'):
        from app.document_attachments import normalize
        normalized, mime, width, height = normalize(data, suffix), "application/x-localmind-document", 0, 0
    else:
        normalized, mime, width, height = normalize_image(data)
    # Display name only; never used as a filesystem path.
    filename = filename.replace("\\", "/").split("/")[-1]
    filename = "".join(c for c in filename if c.isprintable())[:160] or "Screenshot.png"
    key = str(uuid4())
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM chat_attachments WHERE message_id IS NULL AND created_at < ?", (time.time() - DRAFT_TTL,))
        if conversation_id and not conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
            raise ValueError("Conversation not found in this profile.")
        count = conn.execute("SELECT count(*) FROM chat_attachments WHERE draft_id = ? AND message_id IS NULL", (draft_id,)).fetchone()[0]
        if count >= MAX_IMAGES:
            raise ValueError("Attach up to four images per message.")
        used = conn.execute("SELECT coalesce(sum(length(data)),0) FROM chat_attachments").fetchone()[0]
        if used + len(normalized) > MAX_PROFILE_BYTES:
            raise ValueError("Attachment storage is full. Delete unused conversations or drafts.")
        conn.execute("INSERT INTO chat_attachments VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                     (key, draft_id, conversation_id, filename, mime, width, height, normalized, time.time()))
        row = conn.execute("SELECT * FROM chat_attachments WHERE id = ?", (key,)).fetchone()
        return _metadata(row, preview=True)


def preview(key: str, conversation_id=None, draft_id=None) -> dict:
    identifier(key)
    with closing(_get_conn()) as conn:
        row = conn.execute("SELECT * FROM chat_attachments WHERE id = ?", (key,)).fetchone()
        _owner(row, conversation_id, draft_id)
        return _metadata(row, preview=True)


def discard(key: str, draft_id: str) -> None:
    identifier(key)
    identifier(draft_id)
    with closing(_get_conn()) as conn, conn:
        row = conn.execute("SELECT * FROM chat_attachments WHERE id = ?", (key,)).fetchone()
        if row is None:
            return
        if row["draft_id"] != draft_id or row["message_id"]:
            raise ValueError("Only unsent attachments in this draft can be removed.")
        conn.execute("DELETE FROM chat_attachments WHERE id = ?", (key,))


def resolve(ids: list[str], conversation_id: str | None, draft_id: str | None, *, claim=True) -> list[dict]:
    if not ids:
        return []
    if len(ids) > MAX_IMAGES or len(set(ids)) != len(ids):
        raise ValueError("Attach up to four distinct images per message.")
    identifier(conversation_id)
    identifier(draft_id)
    images = []
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
            raise ValueError("Conversation not found in this profile.")
        for key in ids:
            identifier(key)
            row = conn.execute("SELECT * FROM chat_attachments WHERE id = ?", (key,)).fetchone()
            if row is None or row["draft_id"] != draft_id or row["message_id"] or row["conversation_id"] not in (None, conversation_id):
                raise ValueError("An attachment is unavailable or belongs to another conversation.")
            if claim:
                conn.execute("UPDATE chat_attachments SET conversation_id = ? WHERE id = ?", (conversation_id, key))
            if row["mime_type"].startswith("image/"):
                images.append({"mime_type": row["mime_type"], "data": base64.b64encode(row["data"]).decode("ascii")})
    return images


def history_images(message_id: str, conversation_id: str) -> list[dict]:
    with closing(_get_conn()) as conn:
        rows = conn.execute("SELECT mime_type, data FROM chat_attachments WHERE message_id = ? AND conversation_id = ? AND mime_type LIKE 'image/%' ORDER BY created_at, rowid LIMIT 4",
                            (message_id, conversation_id)).fetchall()
    return [{"mime_type": row["mime_type"], "data": base64.b64encode(row["data"]).decode("ascii")} for row in rows]


def document_references(ids, conversation_id, draft_id, message_ids):
    """Owner validation precedes reads; history IDs come from canonical saved history."""
    resolve(ids, conversation_id, draft_id, claim=False)
    with closing(_get_conn()) as conn:
        rows = conn.execute("SELECT * FROM chat_attachments WHERE conversation_id=? AND mime_type='application/x-localmind-document' ORDER BY created_at DESC", (conversation_id,)).fetchall()
        # New drafts have not necessarily been claimed when generating a consent preview.
        if ids:
            placeholders = ','.join('?' for _ in ids)
            rows += conn.execute(f"SELECT * FROM chat_attachments WHERE id IN ({placeholders}) AND conversation_id IS NULL AND mime_type='application/x-localmind-document'", ids).fetchall()
        references = []
        for row in rows:
            if row['id'] not in ids and row['message_id'] not in message_ids:
                continue
            doc = json.loads(row['data'])
            for page in doc['pages']:
                if page['text'].strip():
                    label = f"Document {row['id']} page {page['page']}"
                    references.append(dict(label=label, content=f"{row['filename']} (extracted text only; excerpt may be truncated). Cite [{row['filename']} p.{page['page']}].\n{page['text']}"))
        return references[:120]
