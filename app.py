"""
Illudium-Q36 Analyst Console
--------------------------------
Web UI for multi-file intel ingestion and text / voice chat with Claude.

Usage:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Containers use sticky affinity (see docker-compose) so in-memory chat
history stays on one worker/replica per analyst session.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path

import json

from flask import Flask, Response, jsonify, request, session, send_file, stream_with_context
from werkzeug.utils import secure_filename

import core
import pricing
import report_export
import session_store
import anonymize
import spend_ledger
import transcribe

app = Flask(__name__)
# Prefer a stable secret in containers so sessions survive restarts / multi-worker.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
# Cap whole request body (multi-file uploads); per-file limit still enforced below.
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("MAX_CONTENT_LENGTH", str(16 * 1024 * 1024))
)

_ROOT = Path(__file__).resolve().parent


def _static_asset_version() -> str:
    """Content hash so browsers pick up new CSS/JS after rebuilds."""
    h = hashlib.sha1()
    for rel in ("static/js/app.js", "static/css/app.css"):
        path = _ROOT / rel
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:12]


STATIC_ASSET_VERSION = os.environ.get("STATIC_ASSET_VERSION") or _static_asset_version()


@app.context_processor
def _inject_static_version():
    return {"static_v": STATIC_ASSET_VERSION}

# In-memory chat histories keyed by Flask session id (requires sticky affinity
# when running more than one replica; WEB_CONCURRENCY should stay 1 per replica).
_CHAT_HISTORIES: dict[str, list[dict]] = {}
_USAGE_TOTALS: dict[str, dict] = {}
_LAST_REPORT_BODY: dict[str, str] = {}


def _session_id() -> str:
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


def _history() -> list[dict]:
    sid = _session_id()
    return _CHAT_HISTORIES.setdefault(sid, [])


def _set_history(history: list[dict]) -> None:
    _CHAT_HISTORIES[_session_id()] = history


def _usage_total() -> dict:
    sid = _session_id()
    return _USAGE_TOTALS.setdefault(
        sid,
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "last_model": pricing.resolve_model(None, core.DEFAULT_MODEL),
        },
    )


def _add_usage(delta: dict | None) -> dict:
    sid = _session_id()
    before = float(_usage_total().get("cost_usd") or 0.0)
    _USAGE_TOTALS[sid] = pricing.add_usage(_usage_total(), delta)
    after = float(_USAGE_TOTALS[sid].get("cost_usd") or 0.0)
    spend_ledger.add_spend(after - before)
    return _USAGE_TOTALS[sid]


def _budget_payload() -> dict:
    totals = _usage_total()
    return spend_ledger.status(session_cost_usd=float(totals.get("cost_usd") or 0.0))


def _attachment_response(blob: bytes, mime: str, filename: str):
    """Force a file download across Chrome/Firefox/Edge."""
    import io

    bio = io.BytesIO(blob)
    bio.seek(0)
    resp = send_file(
        bio,
        mimetype=mime or "application/octet-stream",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )
    # Explicit headers — some browsers ignore attachment for text/* types.
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _set_last_report(body: str) -> None:
    _LAST_REPORT_BODY[_session_id()] = body


def _last_report() -> str:
    return _LAST_REPORT_BODY.get(_session_id()) or ""


def _request_model(data: dict | None) -> str:
    requested = (data or {}).get("model") or session.get("model") or core.DEFAULT_MODEL
    model = pricing.resolve_model(requested, core.DEFAULT_MODEL)
    session["model"] = model
    return model


def _limit_mb() -> float:
    if "storage_limit_mb" in session:
        return session_store.clamp_limit_mb(session.get("storage_limit_mb"))
    return session_store.DEFAULT_LIMIT_MB


def _set_limit_mb(value) -> float:
    mb = session_store.clamp_limit_mb(value)
    session["storage_limit_mb"] = mb
    session["storage_limit_set"] = True
    return mb


def _selected_files(data: dict | None) -> list[str] | None:
    """
    files omitted / null → all intel (legacy).
    files: [] → explicitly none.
    files: [...] → that subset.
    """
    if not data or "files" not in data:
        return None
    selected = data.get("files")
    if selected is None:
        return None
    if not isinstance(selected, list):
        raise ValueError("files must be a list of filenames.")
    return [str(x) for x in selected]


def _status_payload(filenames: list[str] | None = None) -> dict:
    return session_store.usage_status(_history(), _limit_mb(), filenames=filenames)


def _reject_if_over(filenames: list[str] | None = None, extra_bytes: int = 0):
    status = _status_payload(filenames=filenames)
    if status["used_bytes"] + extra_bytes > status["limit_bytes"]:
        capture = session_store.build_continuity_capture(
            _history(), _limit_mb(), filenames=filenames
        )
        return (
            jsonify(
                {
                    "error": "Session storage limit reached.",
                    "limit_reached": True,
                    "storage": status,
                    "capture": capture,
                    "capture_markdown": session_store.capture_markdown(capture),
                }
            ),
            413,
        )
    return None


@app.route("/")
def index():
    from flask import make_response, render_template

    resp = make_response(render_template("index.html", static_v=STATIC_ASSET_VERSION))
    # Never cache the shell HTML — it carries the static asset cache-buster.
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.get("/healthz")
@app.get("/api/health")
def healthz():
    """Liveness/readiness probe for Docker, ECS, Cloud Run, GKE, etc."""
    return jsonify(
        {
            "status": "ok",
            "service": "illudium-q36",
            "build_sha": os.environ.get("BUILD_SHA", "unknown"),
            "demo_mode": core.demo_mode_enabled(),
            "static_v": STATIC_ASSET_VERSION,
            "markdown": "tables-headings-v2",
            # If these are missing in /healthz, the container is an older image.
            "features": [
                "model-picker",
                "cost-meter",
                "api-budget",
                "report-export",
                "mermaid",
                "chat-markdown-tables",
                "voice-record-fallback",
                "sse-streaming",
            ],
            "budget": spend_ledger.status(),
            "transcribe": transcribe.status(),
            "sticky_hint": "Use sticky sessions / WEB_CONCURRENCY=1 per replica",
        }
    )


@app.get("/api/transcribe/status")
def api_transcribe_status():
    return jsonify(transcribe.status())


@app.post("/api/transcribe")
def api_transcribe():
    """Firefox / non-Web-Speech browsers: upload recorded audio → text."""
    upload = request.files.get("audio") or request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Missing audio file field (audio)."}), 400
    raw = upload.read()
    if not raw:
        return jsonify({"error": "Empty audio upload."}), 400
    # Cap ~10 MB voice clips
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({"error": "Audio too large (max 10 MB)."}), 413
    try:
        result = transcribe.transcribe_audio(
            raw,
            filename=secure_filename(upload.filename) or "voice.webm",
            content_type=upload.mimetype,
        )
    except transcribe.TranscribeError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Transcription failed: {exc}"}), 500
    return jsonify({"ok": True, **result})


@app.get("/api/session/status")
def api_session_status():
    _session_id()
    return jsonify(
        {
            "sid": session.get("sid"),
            "limit_configured": bool(session.get("storage_limit_set")),
            "default_limit_mb": session_store.DEFAULT_LIMIT_MB,
            "min_limit_mb": session_store.MIN_LIMIT_MB,
            "max_limit_mb": session_store.MAX_LIMIT_MB,
            "storage": _status_payload(),
            "model": session.get("model")
            or pricing.resolve_model(None, core.DEFAULT_MODEL),
            "usage": _usage_total(),
            "budget": _budget_payload(),
        }
    )


@app.post("/api/session/budget")
def api_session_budget():
    data = request.get_json(silent=True) or {}
    try:
        if "reset_spend" in data and data.get("reset_spend"):
            spend_ledger.reset_spend()
        if "budget_usd" in data and data.get("budget_usd") is not None:
            spend_ledger.set_budget(data.get("budget_usd"))
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "budget": _budget_payload()})


@app.get("/api/models")
def api_models():
    return jsonify(
        {
            "models": pricing.list_models_public(),
            "default": pricing.resolve_model(None, core.DEFAULT_MODEL),
        }
    )


@app.post("/api/session/model")
def api_session_model():
    data = request.get_json(silent=True) or {}
    model = _request_model(data)
    return jsonify({"ok": True, "model": model})


@app.post("/api/session/config")
def api_session_config():
    data = request.get_json(silent=True) or {}
    mb = _set_limit_mb(data.get("limit_mb"))
    return jsonify(
        {
            "ok": True,
            "limit_mb": mb,
            "limit_configured": True,
            "storage": _status_payload(),
        }
    )


@app.post("/api/session/capture")
def api_session_capture():
    data = request.get_json(silent=True) or {}
    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    capture = session_store.build_continuity_capture(
        _history(), _limit_mb(), filenames=selected
    )
    return jsonify(
        {
            "capture": capture,
            "capture_markdown": session_store.capture_markdown(capture),
            "storage": capture.get("storage"),
        }
    )


@app.get("/api/session/export.zip")
def api_session_export():
    selected = request.args.getlist("file") or None
    capture = session_store.build_continuity_capture(
        _history(), _limit_mb(), filenames=selected
    )
    blob = session_store.export_session_zip(capture)
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return _attachment_response(
        blob, "application/zip", f"illudium_session_{stamp}.zip"
    )


@app.get("/api/intel")
def api_list_intel():
    return jsonify({"files": core.list_intel_files(), "storage": _status_payload()})


@app.post("/api/intel")
def api_upload_intel():
    """Upload one or more files, and/or paste text as a new intel note."""
    saved: list[str] = []
    errors: list[str] = []

    files = request.files.getlist("files")
    pending_bytes = 0
    buffered: list[tuple[str, bytes]] = []

    for f in files:
        if not f or not f.filename:
            continue
        name = secure_filename(f.filename)
        if not name:
            errors.append("Skipped a file with an empty/invalid name.")
            continue
        # Read at most limit+1 bytes so oversized bodies fail without full buffering.
        data = f.read(core.MAX_FILE_BYTES + 1)
        if len(data) > core.MAX_FILE_BYTES:
            errors.append(
                f"{name}: File exceeds {core.MAX_FILE_BYTES // (1024 * 1024)} MB limit."
            )
            continue
        pending_bytes += len(data)
        buffered.append((name, data))

    pasted = (request.form.get("paste_text") or "").strip()
    paste_name = (request.form.get("paste_name") or "").strip()
    paste_bytes = 0
    if pasted:
        paste_bytes = len(pasted.encode("utf-8"))
        pending_bytes += paste_bytes

    blocked = _reject_if_over(extra_bytes=pending_bytes)
    if blocked:
        return blocked

    batch = anonymize.Anonymizer()
    upload_count = len(buffered) + (1 if pasted else 0)

    for name, data in buffered:
        try:
            if anonymize.is_text_intel(name):
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                data = batch.redact_text(text).encode("utf-8")
            saved.append(core.save_intel_bytes(name, data))
        except Exception as exc:  # noqa: BLE001 — surface to analyst UI
            errors.append(f"{name}: {exc}")

    if pasted:
        if not paste_name:
            paste_name = f"paste_{uuid.uuid4().hex[:8]}.txt"
        paste_name = secure_filename(paste_name)
        if not Path(paste_name).suffix:
            paste_name += ".txt"
        try:
            redacted_paste = batch.redact_text(pasted)
            saved.append(core.save_intel_text(paste_name, redacted_paste))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{paste_name}: {exc}")

    if not saved and not errors:
        return jsonify({"error": "No files or paste text provided."}), 400

    summary = batch.summary()
    return jsonify(
        {
            "saved": saved,
            "errors": errors,
            "files": core.list_intel_files(),
            "storage": _status_payload(),
            "anonymization": {
                "warning": anonymize.warning_message(upload_count),
                "file_count": upload_count,
                **summary,
            },
        }
    )


@app.delete("/api/intel/<path:filename>")
def api_delete_intel(filename: str):
    try:
        core.delete_intel_file(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "files": core.list_intel_files(), "storage": _status_payload()})


@app.get("/api/intel/<path:filename>")
def api_read_intel(filename: str):
    try:
        content = core.read_intel_file(filename)
    except FileNotFoundError:
        return jsonify({"error": "File not found."}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"name": filename, "content": content})


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    blocked = _reject_if_over(
        filenames=selected, extra_bytes=len(message.encode("utf-8"))
    )
    if blocked:
        return blocked

    corpus = core.load_intel_corpus(filenames=selected)
    history = _history()
    model = _request_model(data)

    try:
        reply, updated, call_usage = core.chat_with_intel(
            message, history, corpus, model=model
        )
        _set_history(updated)
        _set_last_report(reply)
        totals = _add_usage(call_usage)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    status = _status_payload(filenames=selected)
    payload = {
        "reply": reply,
        "history": updated,
        "intel_files_used": selected
        if selected is not None
        else [f["name"] for f in core.list_intel_files()],
        "storage": status,
        "model": model,
        "usage": call_usage,
        "usage_total": totals,
        "budget": _budget_payload(),
    }
    if status["limit_reached"]:
        capture = session_store.build_continuity_capture(
            updated, _limit_mb(), filenames=selected
        )
        payload["limit_reached"] = True
        payload["capture"] = capture
        payload["capture_markdown"] = session_store.capture_markdown(capture)
    return jsonify(payload)


@app.post("/api/chat/stream")
def api_chat_stream():
    """SSE chat: token events, then a final done payload (lower perceived latency)."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    blocked = _reject_if_over(
        filenames=selected, extra_bytes=len(message.encode("utf-8"))
    )
    if blocked:
        return blocked

    corpus = core.load_intel_corpus(filenames=selected)
    history = _history()
    model = _request_model(data)

    @stream_with_context
    def generate():
        try:
            yield _sse({"type": "status", "text": "Analyzing…"})
            for event in core.iter_chat_tokens(message, history, corpus, model=model):
                kind = event[0]
                if kind == "token":
                    yield _sse({"type": "token", "text": event[1]})
                elif kind == "done":
                    _reply, updated, call_usage = event[1], event[2], event[3]
                    _set_history(updated)
                    _set_last_report(_reply)
                    totals = _add_usage(call_usage)
                    status = _status_payload(filenames=selected)
                    payload = {
                        "type": "done",
                        "reply": _reply,
                        "history": updated,
                        "intel_files_used": selected
                        if selected is not None
                        else [f["name"] for f in core.list_intel_files()],
                        "storage": status,
                        "model": model,
                        "usage": call_usage,
                        "usage_total": totals,
                        "budget": _budget_payload(),
                    }
                    if status["limit_reached"]:
                        capture = session_store.build_continuity_capture(
                            updated, _limit_mb(), filenames=selected
                        )
                        payload["limit_reached"] = True
                        payload["capture"] = capture
                        payload["capture_markdown"] = session_store.capture_markdown(
                            capture
                        )
                    yield _sse(payload)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "error": str(exc)})

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.post("/api/chat/clear")
def api_chat_clear():
    sid = _session_id()
    _set_history([])
    _USAGE_TOTALS[sid] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "last_model": session.get("model")
        or pricing.resolve_model(None, core.DEFAULT_MODEL),
    }
    _LAST_REPORT_BODY.pop(sid, None)
    return jsonify(
        {
            "ok": True,
            "history": [],
            "storage": _status_payload(),
            "usage_total": _usage_total(),
            "budget": _budget_payload(),
        }
    )


@app.get("/api/chat/history")
def api_chat_history():
    return jsonify(
        {
            "history": _history(),
            "storage": _status_payload(),
            "limit_configured": bool(session.get("storage_limit_set")),
            "model": session.get("model")
            or pricing.resolve_model(None, core.DEFAULT_MODEL),
            "usage_total": _usage_total(),
            "budget": _budget_payload(),
        }
    )


@app.get("/api/atlas")
def api_atlas_summary():
    """Return ATLAS bridge metadata and optional overlap analysis for given IDs/text."""
    import atlas

    attack_ids = request.args.getlist("attack_id")
    text = request.args.get("text") or ""
    analysis = atlas.analyze_overlap(attack_ids=attack_ids or None, source_text=text)
    bridge = atlas.load_bridge()
    return jsonify(
        {
            "data_version": bridge.get("data_version"),
            "source_url": bridge.get("source_url"),
            "tactic_count": len(bridge.get("tactics") or []),
            "overlap_count": len(bridge.get("overlaps") or []),
            "analysis": analysis,
        }
    )


@app.post("/api/briefing")
def api_briefing():
    data = request.get_json(silent=True) or {}
    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    extra = data.get("extra_text") or ""

    blocked = _reject_if_over(filenames=selected, extra_bytes=len(extra.encode("utf-8")))
    if blocked:
        return blocked

    corpus = core.load_intel_corpus(filenames=selected, extra_text=extra)
    if not corpus.strip():
        return jsonify({"error": "No intelligence loaded. Upload or paste intel first."}), 400

    model = _request_model(data)
    try:
        briefing, atlas_analysis, call_usage = core.generate_briefing_with_atlas(
            corpus, model=model
        )
        path = core.save_briefing(briefing)
        _set_last_report(briefing)
        totals = _add_usage(call_usage)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    history = _history()
    history.append(
        {
            "role": "user",
            "content": "Generate an executive briefing from the currently loaded intelligence.",
        }
    )
    history.append({"role": "assistant", "content": briefing})
    _set_history(history)

    status = _status_payload(filenames=selected)
    payload = {
        "briefing": briefing,
        "path": path,
        "history": history,
        "atlas": atlas_analysis,
        "storage": status,
        "model": model,
        "usage": call_usage,
        "usage_total": totals,
        "budget": _budget_payload(),
    }
    if status["limit_reached"]:
        capture = session_store.build_continuity_capture(
            history, _limit_mb(), filenames=selected
        )
        payload["limit_reached"] = True
        payload["capture"] = capture
        payload["capture_markdown"] = session_store.capture_markdown(capture)
    return jsonify(payload)


@app.post("/api/briefing/stream")
def api_briefing_stream():
    """SSE briefing generation with progressive tokens."""
    data = request.get_json(silent=True) or {}
    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    extra = data.get("extra_text") or ""

    blocked = _reject_if_over(filenames=selected, extra_bytes=len(extra.encode("utf-8")))
    if blocked:
        return blocked

    corpus = core.load_intel_corpus(filenames=selected, extra_text=extra)
    if not corpus.strip():
        return jsonify(
            {"error": "No intelligence loaded. Upload or paste intel first."}
        ), 400

    model = _request_model(data)

    @stream_with_context
    def generate():
        try:
            yield _sse({"type": "status", "text": "Generating briefing…"})
            for event in core.iter_briefing_tokens(corpus, model=model):
                kind = event[0]
                if kind == "token":
                    yield _sse({"type": "token", "text": event[1]})
                elif kind == "done":
                    briefing, atlas_analysis, call_usage = (
                        event[1],
                        event[2],
                        event[3],
                    )
                    path = core.save_briefing(briefing)
                    _set_last_report(briefing)
                    totals = _add_usage(call_usage)
                    history = _history()
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "Generate an executive briefing from the currently "
                                "loaded intelligence."
                            ),
                        }
                    )
                    history.append({"role": "assistant", "content": briefing})
                    _set_history(history)
                    status = _status_payload(filenames=selected)
                    payload = {
                        "type": "done",
                        "briefing": briefing,
                        "path": path,
                        "history": history,
                        "atlas": atlas_analysis,
                        "storage": status,
                        "model": model,
                        "usage": call_usage,
                        "usage_total": totals,
                        "budget": _budget_payload(),
                    }
                    if status["limit_reached"]:
                        capture = session_store.build_continuity_capture(
                            history, _limit_mb(), filenames=selected
                        )
                        payload["limit_reached"] = True
                        payload["capture"] = capture
                        payload["capture_markdown"] = session_store.capture_markdown(
                            capture
                        )
                    yield _sse(payload)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "error": str(exc)})

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.post("/api/report")
def api_report():
    """Build a downloadable report in md, pdf, or docx."""
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "md").lower()
    if fmt not in {"md", "pdf", "docx", "doc"}:
        return jsonify({"error": "format must be md, pdf, or docx."}), 400

    try:
        selected = _selected_files(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    regenerate = bool(data.get("regenerate", True))
    model = _request_model(data)
    body = _last_report()
    call_usage = None
    atlas_analysis = None
    path = None

    if regenerate or not body.strip():
        corpus = core.load_intel_corpus(filenames=selected)
        if not corpus.strip():
            return jsonify({"error": "No intelligence loaded. Upload intel first."}), 400
        try:
            body, atlas_analysis, call_usage = core.generate_briefing_with_atlas(
                corpus, model=model
            )
            path = core.save_briefing(body)
            _set_last_report(body)
            totals = _add_usage(call_usage)
            history = _history()
            history.append(
                {
                    "role": "user",
                    "content": f"Create an analyst report for download ({fmt}).",
                }
            )
            history.append({"role": "assistant", "content": body})
            _set_history(history)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500
    else:
        totals = _usage_total()

    md = report_export.build_report_markdown(
        title="Illudium-Q36 Analyst Report",
        body=body,
        model=model,
        usage=totals,
        atlas_note=(
            f"ATLAS data {atlas_analysis.get('data_version')}"
            if atlas_analysis and atlas_analysis.get("data_version")
            else None
        ),
    )
    try:
        blob, mime, filename = report_export.export_report(md, fmt)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Export failed: {exc}"}), 500

    return _attachment_response(blob, mime, filename)


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": "Upload too large for configured MAX_CONTENT_LENGTH."}), 413


if __name__ == "__main__":
    core.ensure_dirs()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"}
    print(f"Illudium-Q36 Analyst Console → http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
