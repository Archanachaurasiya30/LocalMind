"""Bounded inert media files; never return or execute arbitrary provider content."""
import base64
import binascii
import struct
import zlib
from urllib.parse import urlparse

import httpx

MAX_VIDEO = 100 * 1024 * 1024
MAX_IMAGE = 3_500_000  # Leaves room for the provider's 5 MB base64 URI limit.


def png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 33:
        raise ValueError("Choose a valid PNG image.")
    offset, width, height, image_data = 8, 0, 0, False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = bytes(data[offset + 4:offset + 8])
        end = offset + 12 + length
        if end > len(data):
            break
        payload = data[offset + 8:end - 4]
        if zlib.crc32(kind + payload) != int.from_bytes(data[end - 4:end], "big"):
            break
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                break
            width, height = struct.unpack(">II", payload[:8])
            if not (1 <= width <= 4096 and 1 <= height <= 4096):
                raise ValueError("PNG dimensions must be between 1 and 4096 pixels.")
        image_data |= kind == b"IDAT" and length > 0
        if kind == b"IEND" and length == 0 and end == len(data) and image_data:
            return width, height
        offset = end
    raise ValueError("The PNG is damaged or has trailing content. Export it again as PNG.")


def decode_image(uri):
    prefix = "data:image/png;base64,"
    if not uri.startswith(prefix) or len(uri) > 4_700_000:
        raise ValueError("Use a PNG smaller than 3.5 MB.")
    try:
        data = base64.b64decode(uri[len(prefix):], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("The image could not be read.") from None
    if len(data) > MAX_IMAGE:
        raise ValueError("Use a PNG smaller than 3.5 MB.")
    png(data)
    return data


def video(data):
    # Walk ISO BMFF boxes, rejecting HTML, executables, truncation and trailers.
    offset, kinds = 0, set()
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = bytes(data[offset + 4:offset + 8])
        header = 8
        if size == 1 and offset + 16 <= len(data):
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            break
        if not kinds and (kind != b"ftyp" or size < 16):
            break
        kinds.add(kind)
        offset += size
    if offset != len(data) or not {b"ftyp", b"moov", b"mdat"}.issubset(kinds):
        raise ValueError("Runway returned an invalid MP4. No output was saved.")


async def download(url, destination):
    parsed = urlparse(url)
    # Runway-owned output domains only. No credentials, redirects or arbitrary hosts.
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not (host == "cloudfront.net" or host.endswith(".cloudfront.net")
                    or host.endswith(".runwayml.com") or host.endswith(".runway.com"))):
        raise ValueError("Unrecognized Runway download host. Check the task in Runway; no download was made.")
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0] != "video/mp4":
                raise ValueError("Runway did not return an MP4 file.")
            if int(response.headers.get("content-length", "0")) > MAX_VIDEO:
                raise ValueError("The video exceeds the 100 MB download limit.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_VIDEO:
                    raise ValueError("The video exceeds the 100 MB download limit.")
                data.extend(chunk)
    video(data)
    temporary = destination.with_suffix(".part")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
