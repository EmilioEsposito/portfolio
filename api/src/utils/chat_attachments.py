"""Validate private-chat attachments before persistence or provider dispatch.

Files remain inline data, never filesystem paths or remotely fetched URLs.
Validation establishes format, not document trust or absence of malicious content.
"""

import asyncio
import base64
import binascii
import io
import json
import re
import warnings
from pathlib import PurePosixPath
from urllib.parse import unquote

from fastapi import HTTPException, Request
from PIL import Image
from pypdf import PdfReader

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_FILES = 5
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
EXTENSIONS = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg", ".jfif"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
}
_DATA_URL = re.compile(r"data:([^;,]+);base64,([A-Za-z0-9+/=]+)\Z")


def _invalid() -> HTTPException:
    # Never echo filenames, URLs, parser errors or submitted content.
    return HTTPException(400, "Invalid attachment. Use a valid JPEG, PNG, GIF, WebP or PDF file.")


def _validate_filename(filename: object, media_type: str) -> None:
    if filename is None:
        return
    if not isinstance(filename, str) or not filename or len(filename) > 255:
        raise _invalid()
    # Reject encoded path/control characters too; the name is display metadata only.
    decoded = filename
    for _ in range(3):
        decoded = unquote(decoded)
    if any(c in decoded for c in ("/", "\\", ":", "%")) or any(
        ord(c) < 32 or ord(c) == 127 for c in decoded
    ):
        raise _invalid()
    if decoded in {".", ".."} or decoded != decoded.strip():
        raise _invalid()
    suffix = PurePosixPath(decoded).suffix.lower()
    if suffix and suffix not in EXTENSIONS[media_type]:
        raise _invalid()


def _validate_content(content: bytes, media_type: str) -> None:
    try:
        if media_type == "application/pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("Not a PDF")
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF")
            # Resolve the root, without rendering or executing any document actions.
            if reader.trailer["/Root"].get("/Type") != "/Catalog":
                raise ValueError("Missing PDF catalog")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content), formats=list(IMAGE_TYPES)) as image:
                    if IMAGE_TYPES.get(image.format) != media_type:
                        raise ValueError("Mismatched image type")
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("Image too large")
                    image.verify()
                # verify() alone does not decode JPEG/WebP pixel data.
                with Image.open(io.BytesIO(content), formats=list(IMAGE_TYPES)) as image:
                    image.load()
    except Exception:
        raise _invalid() from None


def validate_chat_attachments(body: object) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a chat JSON object.")
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        raise HTTPException(400, "Invalid chat messages.")
    # Both private routes forward only the latest message; history is server-owned.
    if not messages:
        return body
    message = messages[-1]
    if not isinstance(message, dict) or not isinstance(message.get("parts", []), list):
        raise HTTPException(400, "Invalid chat message.")
    files = 0
    total = 0
    for part in message.get("parts", []):
        if not isinstance(part, dict):
            raise HTTPException(400, "Invalid chat message part.")
        if part.get("type") != "file":
            continue
        files += 1
        if files > MAX_FILES:
            raise HTTPException(413, "Too many attachments (maximum 5).")
        media_type = part.get("mediaType")
        url = part.get("url")
        if (
            not isinstance(media_type, str)
            or media_type not in EXTENSIONS
            or not isinstance(url, str)
        ):
            raise _invalid()
        if len(url) > 4 * ((MAX_FILE_BYTES + 2) // 3) + 100:
            raise HTTPException(413, "Attachment exceeds 5 MB.")
        match = _DATA_URL.fullmatch(url)
        if not match or match[1] != media_type:
            raise _invalid()
        try:
            content = base64.b64decode(match[2], validate=True)
        except (binascii.Error, ValueError):
            raise _invalid() from None
        if not content:
            raise _invalid()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, "Attachment exceeds 5 MB.")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Attachments exceed 10 MB in total.")
        _validate_filename(part.get("filename"), media_type)
        _validate_content(content, media_type)
    return body


async def read_chat_request(request: Request) -> dict:
    """Bound streaming input before JSON/base64 allocation, after route authentication."""
    raw = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                    raise HTTPException(413, "Chat request exceeds 16 MB.")
                raw.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Chat request timed out.") from None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "Invalid chat JSON.") from None
    return await asyncio.to_thread(validate_chat_attachments, body)
