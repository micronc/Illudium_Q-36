"""
Server-side speech-to-text for browsers without Web Speech Recognition (Firefox).

Uses OpenAI Whisper or Groq Whisper when a key is configured.
"""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request


class TranscribeError(RuntimeError):
    pass


def provider() -> str | None:
    if os.environ.get("DEMO_MODE", "").lower() in {"1", "true", "yes"}:
        return "demo"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if os.environ.get("GROQ_API_KEY", "").strip():
        return "groq"
    return None


def status() -> dict[str, Any]:
    p = provider()
    return {
        "available": p is not None,
        "provider": p,
        "note": (
            "Firefox has no Web Speech Recognition. Illudium records mic audio and "
            "transcribes it with Whisper when OPENAI_API_KEY or GROQ_API_KEY is set "
            "(or DEMO_MODE for a sample transcript)."
            if p != "demo"
            else "Demo mode returns a sample transcript (no live Whisper call)."
        ),
    }


def _guess_filename(content_type: str | None, fallback: str) -> str:
    if fallback and Path(fallback).suffix:
        return Path(fallback).name
    ctype = (content_type or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(ctype) or ".webm"
    if ext == ".jpe":
        ext = ".jpg"
    return f"voice{ext}"


def _multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = f"----IlludiumBoundary{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def _post_whisper(
    url: str,
    api_key: str,
    audio: bytes,
    filename: str,
    content_type: str,
    *,
    model: str,
) -> str:
    body, ctype = _multipart(
        {"model": model, "response_format": "json"},
        "file",
        filename,
        audio,
        content_type or "application/octet-stream",
    )

    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": ctype,
        },
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TranscribeError(f"Transcription API error ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise TranscribeError(f"Transcription request failed: {exc.reason}") from exc

    text = (payload.get("text") or "").strip()
    if not text:
        raise TranscribeError("Transcription returned empty text.")
    return text


def transcribe_audio(
    audio: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    if not audio:
        raise TranscribeError("Empty audio upload.")

    p = provider()
    if not p:
        raise TranscribeError(
            "No transcription provider configured. Set OPENAI_API_KEY or GROQ_API_KEY "
            "for Firefox voice input (Whisper), or use Chrome/Edge for built-in recognition."
        )

    name = _guess_filename(content_type, filename or "voice.webm")

    if p == "demo":
        return {
            "text": "Summarize the IOCs across the loaded files.",
            "provider": "demo",
        }

    if p == "openai":
        text = _post_whisper(
            "https://api.openai.com/v1/audio/transcriptions",
            os.environ["OPENAI_API_KEY"].strip(),
            audio,
            name,
            content_type or "audio/webm",
            model="whisper-1",
        )
        return {"text": text, "provider": "openai"}

    text = _post_whisper(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        os.environ["GROQ_API_KEY"].strip(),
        audio,
        name,
        content_type or "audio/webm",
        model="whisper-large-v3",
    )
    return {"text": text, "provider": "groq"}
