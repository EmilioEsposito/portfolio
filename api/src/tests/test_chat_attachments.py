"""Attachment boundary regression tests; synthetic bytes, no provider calls."""

import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfWriter
from starlette.requests import Request

from api.src.utils import chat_attachments as uploads


def image_bytes(fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format=fmt)
    return output.getvalue()


def pdf_bytes():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return output.getvalue()


def file_part(content=None, mime="image/png", filename="photo.png"):
    content = image_bytes() if content is None else content
    return {
        "type": "file",
        "mediaType": mime,
        "filename": filename,
        "url": f"data:{mime};base64," + base64.b64encode(content).decode(),
    }


def body(*parts):
    return {"messages": [{"role": "user", "parts": list(parts)}]}


def request(data):
    chunks = iter(data)

    async def bounded_receive():
        chunk = next(chunks, None)
        return {"type": "http.request", "body": chunk or b"", "more_body": chunk is not None}

    return Request({"type": "http", "headers": []}, bounded_receive)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_real_images(fmt):
    mime = uploads.IMAGE_TYPES[fmt]
    extension = next(iter(uploads.EXTENSIONS[mime]))
    payload = body(file_part(image_bytes(fmt), mime, "photo" + extension))
    assert uploads.validate_chat_attachments(payload) == payload


def test_real_pdf_and_text():
    payload = body(
        {"type": "text", "text": "Review this"},
        file_part(pdf_bytes(), "application/pdf", "lease.pdf"),
    )
    assert uploads.validate_chat_attachments(payload) == payload
    assert uploads.validate_chat_attachments(body({"type": "text", "text": "hi"}))


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.png",
        "/tmp/escape.png",
        "..\\escape.png",
        "C:\\escape.png",
        "a\x00.png",
        "a\r\n.png",
        "%2e%2e%2fescape.png",
        "%252e%252e%252fescape.png",
        "photo.html",
        "x" * 256,
        " photo.png",
    ],
)
def test_reject_unsafe_names(filename):
    with pytest.raises(HTTPException) as error:
        uploads.validate_chat_attachments(body(file_part(filename=filename)))
    assert error.value.status_code == 400
    assert filename not in error.value.detail


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.png",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "data:image/png;base64,@@@",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/jpeg;base64,YQ==",
    ],
)
def test_reject_remote_urls_and_malformed_data(url):
    part = file_part()
    part["url"] = url
    with pytest.raises(HTTPException):
        uploads.validate_chat_attachments(body(part))


@pytest.mark.parametrize(
    "content,mime,name",
    [
        (b"<script>bad()</script>", "image/png", "image.png"),
        (b"%PDF-1.7\n<html>not a PDF</html>", "application/pdf", "file.pdf"),
        (image_bytes("JPEG"), "image/png", "image.png"),
        (b"GIF89a", "image/gif", "image.gif"),
        (image_bytes()[:30], "image/png", "image.png"),
        (b"", "image/png", "image.png"),
    ],
)
def test_spoofed_or_truncated_content(content, mime, name):
    with pytest.raises(HTTPException) as error:
        uploads.validate_chat_attachments(body(file_part(content, mime, name)))
    assert error.value.status_code == 400
    assert "Traceback" not in error.value.detail


def test_limits_before_parser(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 2)
    parser = AsyncMock()
    monkeypatch.setattr(uploads, "_validate_content", parser)
    with pytest.raises(HTTPException) as error:
        uploads.validate_chat_attachments(body(file_part()))
    assert error.value.status_code == 413
    parser.assert_not_called()


def test_count_and_total_limits(monkeypatch):
    with pytest.raises(HTTPException) as error:
        uploads.validate_chat_attachments(body(*([file_part()] * 6)))
    assert error.value.status_code == 413
    monkeypatch.setattr(uploads, "MAX_TOTAL_BYTES", len(image_bytes()))
    with pytest.raises(HTTPException) as error:
        uploads.validate_chat_attachments(body(file_part(), file_part()))
    assert error.value.status_code == 413


def test_pixel_limit(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_IMAGE_PIXELS", 3)
    with pytest.raises(HTTPException):
        uploads.validate_chat_attachments(body(file_part()))


@pytest.mark.asyncio
async def test_streaming_body_limit_and_invalid_json(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_REQUEST_BYTES", 10)
    with pytest.raises(HTTPException) as error:
        await uploads.read_chat_request(request([b"12345", b"123456"]))
    assert error.value.status_code == 413
    with pytest.raises(HTTPException) as error:
        await uploads.read_chat_request(request([b"invalid"]))
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_valid_streamed_body():
    payload = body(file_part())
    raw = json.dumps(payload).encode()
    assert await uploads.read_chat_request(request([raw[:20], raw[20:]])) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,handler",
    [("api.src.sernia_ai.routes", "chat_sernia"), ("api.src.ai_demos.hitl_agents.routes", "chat")],
)
async def test_both_routes_reject_before_history_or_provider(monkeypatch, module, handler):
    import importlib

    routes = importlib.import_module(module)
    history = AsyncMock(side_effect=AssertionError("Database must not be reached"))
    monkeypatch.setattr(routes, "get_conversation_messages", history)
    raw = json.dumps(body(file_part(b"<html>spoof</html>"))).encode()
    user = SimpleNamespace(id="test-user", first_name="Test", last_name="User")
    with pytest.raises(HTTPException) as error:
        await getattr(routes, handler)(request([raw]), user=user, session=None)
    assert error.value.status_code == 400
    history.assert_not_called()
