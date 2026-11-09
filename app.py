import base64
import logging
import os
import re
import threading
import time
import uuid
import wave
from collections import defaultdict, deque
from functools import wraps

from flask import Flask, Response, g, jsonify, render_template, request
from jinja2 import TemplateNotFound
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import database
from ai_brain import AIError, generate_answer
from config import Config, get_language, validate_config
from stt import STTError, transcribe_audio
from tts import TTSError, synthesize_speech

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("voicemate")

# ---------- Start-up: fail early with a clear message if something is missing ----------
validate_config()
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
database.init_db()

app = Flask(__name__)
# Render sits in front of the app. This lets Flask see the real visitor IP and that the page is https.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH   # Flask rejects bigger uploads
app.json.ensure_ascii = False   # send Hindi/Gujarati as real characters, not \u escapes


# =====================================================================
# Errors: one consistent JSON shape for every failure
# =====================================================================
class ApiError(Exception):
    """An error raised by our own route code."""

    def __init__(self, message, code, status=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


# Errors caused by what the user sent (HTTP 400)
USER_ERROR_CODES = {
    "unsupported_language", "empty_audio", "too_short", "too_long", "bad_format",
    "invalid_audio", "bad_sample_rate", "no_file", "empty_question", "empty_text",
    "bad_request", "no_speech",
}


def status_for(code):
    """Pick the HTTP status for one of our error codes."""
    if code == "rate_limit":
        return 429
    if code == "timeout":
        return 504
    if code == "too_large":
        return 413
    if code in USER_ERROR_CODES:
        return 400
    return 502   # a problem with an outside service (Gnani, Sarvam)


def error_response(message, code, status=None):
    body = {"success": False, "error": {"code": code, "message": message}}
    return jsonify(body), (status or status_for(code))


@app.errorhandler(ApiError)
def handle_api_error(e):
    return error_response(e.message, e.code, e.status)


@app.errorhandler(STTError)
@app.errorhandler(AIError)
@app.errorhandler(TTSError)
def handle_service_error(e):
    # These carry friendly messages written in stt.py, ai_brain.py and tts.py
    return error_response(e.message, e.code)


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(e):
    return error_response("That file is too large. Please send a shorter recording.", "too_large", 413)


@app.errorhandler(Exception)
def handle_unexpected(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return error_response(e.description, "http_error", e.code)
        return e
    logger.exception("Unexpected error")   # full details go to the log, not to the user
    return error_response("Something went wrong on the server. Please try again.", "server_error", 500)


# =====================================================================
# Who is this browser? (a random private id in a cookie: no login needed)
# =====================================================================
CLIENT_COOKIE = "voicemate_client"
CLIENT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


@app.before_request
def identify_client():
    cookie_value = request.cookies.get(CLIENT_COOKIE, "")
    if CLIENT_ID_PATTERN.match(cookie_value):
        g.client_id = cookie_value
        g.new_client = False
    else:
        g.client_id = uuid.uuid4().hex
        g.new_client = True


@app.after_request
def remember_client(response):
    if getattr(g, "new_client", False):
        response.set_cookie(
            CLIENT_COOKIE,
            g.client_id,
            max_age=60 * 60 * 24 * 365,   # one year
            httponly=True,                # JavaScript cannot read it
            samesite="Lax",
            secure=request.is_secure,     # True automatically on https (works because of ProxyFix)
        )
    return response


# =====================================================================
# Small helpers
# =====================================================================
def get_json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def parse_language(raw):
    lang = raw or Config.DEFAULT_LANGUAGE
    if not isinstance(lang, str) or get_language(lang) is None:
        supported = ", ".join(Config.LANGUAGES)
        raise ApiError(
            f"Language '{lang}' is not supported. Choose one of: {supported}.",
            "unsupported_language",
        )
    return lang


def parse_conversation_id(raw):
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError("Invalid conversation id.", "bad_request", 400)


def parse_bool(raw):
    return raw is True or str(raw).strip().lower() in ("1", "true", "yes", "on")


def audio_payload(wav_bytes):
    """Generated speech travels inside the JSON as base64 text (it is never saved)."""
    return {"mime": "audio/wav", "base64": base64.b64encode(wav_bytes).decode("ascii")}


def wav_seconds(path):
    """Length of a WAV file, measured from its real size (headers can be wrong)."""
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            frame_bytes = w.getnchannels() * w.getsampwidth()
        return max(0, (os.path.getsize(path) - 44) // frame_bytes) / float(rate)
    except Exception:
        return 0.0


# =====================================================================
# Rate limit: protects your Gnani and Sarvam credits on a public site
# =====================================================================
RATE_LIMIT = 30        # requests that cost credits, per visitor ...
RATE_WINDOW = 600      # ... in this many seconds (10 minutes)
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.remote_addr or "unknown"
        if key in ("127.0.0.1", "::1"):        # your own computer is never limited
            return fn(*args, **kwargs)
        now = time.time()
        with _hits_lock:
            hits = _hits[key]
            while hits and now - hits[0] > RATE_WINDOW:
                hits.popleft()
            if len(hits) >= RATE_LIMIT:
                minutes = max(1, int(RATE_WINDOW - (now - hits[0])) // 60 + 1)
                raise ApiError(
                    f"You’ve reached the demo limit. Please try again in about {minutes} minute(s).",
                    "rate_limit", 429,
                )
            hits.append(now)
        return fn(*args, **kwargs)
    return wrapper


# =====================================================================
# Pages and basic info
# =====================================================================
@app.get("/")
def index():
    try:
        return render_template("index.html")
    except TemplateNotFound:
        return (
            "<h2>VoiceMate AI backend is running.</h2>"
            "<p>Try <a href='/api/health'>/api/health</a> or "
            "<a href='/api/config'>/api/config</a>.</p>"
        )


@app.get("/api/health")
def api_health():
    return jsonify({
        "success": True,
        "status": "ok",
        "models": {"stt": Config.STT_MODEL, "tts": Config.TTS_MODEL, "llm": Config.LLM_MODEL},
    })


@app.get("/api/config")
def api_config():
    return jsonify({
        "success": True,
        "default_language": Config.DEFAULT_LANGUAGE,
        "languages": [
            {"key": key, "label": info["label"], "native": info["native"]}
            for key, info in Config.LANGUAGES.items()
        ],
        "limits": {
            "max_audio_seconds": Config.MAX_AUDIO_SECONDS,
            "max_question_chars": Config.MAX_QUESTION_CHARS,
        },
    })


# =====================================================================
# Speech to text:  audio file  ->  text  (the recording is saved for "Recent voice sessions")
# =====================================================================
@app.post("/api/transcribe")
@rate_limited
def api_transcribe():
    lang = parse_language(request.form.get("language"))

    file = request.files.get("audio")
    if file is None or not file.filename:
        raise ApiError("No audio was received. Please record again.", "no_file", 400)

    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    if ext not in Config.ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(Config.ALLOWED_AUDIO_EXTENSIONS))
        raise ApiError(f"Unsupported audio type '.{ext}'. Allowed: {allowed}.", "bad_format", 400)

    # We choose the file name ourselves, so a user-supplied name can never be used as a path
    path = os.path.join(Config.UPLOAD_FOLDER, f"{uuid.uuid4().hex}.{ext}")
    file.save(path)
    recording_id = None
    try:
        result = transcribe_audio(path, lang)    # raises on failure, so bad audio is never saved
        if ext == "wav":
            with open(path, "rb") as f:
                recording_id = database.save_recording(
                    g.client_id, f.read(), wav_seconds(path), lang
                )
    finally:
        try:
            os.remove(path)   # the temporary file is always removed
        except OSError:
            logger.warning("Could not delete temporary file %s", path)

    return jsonify({
        "success": True,
        "text": result["text"],
        "language": lang,
        "detected_language": result["detected_language"],
        "model": result["model"],
        "recording_id": recording_id,
    })


# =====================================================================
# Chat:  text  ->  answer (+ optional audio), saved to history
# =====================================================================
@app.post("/api/chat")
@rate_limited
def api_chat():
    body = get_json_body()

    lang = parse_language(body.get("language"))

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ApiError("Please type or say a question first.", "empty_question", 400)
    text = text.strip()
    if len(text) > Config.MAX_QUESTION_CHARS:
        raise ApiError(
            f"That question is too long. Please keep it under {Config.MAX_QUESTION_CHARS} characters.",
            "too_long", 400,
        )

    input_type = body.get("input_type", "text")
    if input_type not in ("text", "voice"):
        input_type = "text"
    voice_output = parse_bool(body.get("voice_output"))
    conv_id = parse_conversation_id(body.get("conversation_id"))

    # Existing conversation -> load its history. No id -> start a new conversation.
    created_now = False
    if conv_id is None:
        conv_id = database.create_conversation(g.client_id, lang)["id"]
        created_now = True
        history = []
    else:
        try:
            history = database.get_history_for_llm(g.client_id, conv_id)
        except database.ConversationNotFound:
            raise ApiError("Conversation not found.", "not_found", 404)

    # Ask the LLM. If it fails, do not leave an empty conversation in the sidebar.
    try:
        answer = generate_answer(text, history, lang)["text"]
    except Exception:
        if created_now:
            database.delete_conversation(g.client_id, conv_id)
        raise

    # Save both messages only after we have a good answer
    user_message = database.add_message(g.client_id, conv_id, "user", text, lang, input_type)
    assistant_message = database.add_message(g.client_id, conv_id, "assistant", answer, lang, "text")

    # Link the voice recording (saved by /api/transcribe) to the question it became
    if input_type == "voice":
        try:
            recording_id = int(body.get("recording_id"))
        except (TypeError, ValueError):
            recording_id = None
        if recording_id is not None:
            database.attach_recording(g.client_id, recording_id, conv_id, user_message["id"])

    # Voice output is a bonus: if Timbre fails, the user still gets the written answer
    audio = None
    audio_error = None
    if voice_output:
        try:
            audio = audio_payload(synthesize_speech(answer, lang))
        except TTSError as e:
            logger.warning("Voice output failed [%s]: %s", e.code, e.message)
            audio_error = e.message

    return jsonify({
        "success": True,
        "conversation": database.get_conversation(g.client_id, conv_id),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "audio": audio,
        "audio_error": audio_error,
    })


# =====================================================================
# "Listen" button: speak an answer that is already saved in history
# =====================================================================
@app.post("/api/conversations/<int:conv_id>/messages/<int:message_id>/speech")
@rate_limited
def api_message_speech(conv_id, message_id):
    try:
        messages = database.get_messages(g.client_id, conv_id)
    except database.ConversationNotFound:
        raise ApiError("Conversation not found.", "not_found", 404)

    message = next((m for m in messages if m["id"] == message_id), None)
    if message is None or message["role"] != "assistant":
        raise ApiError("Message not found.", "not_found", 404)

    wav = synthesize_speech(message["content"], message["language"])
    return jsonify({"success": True, "audio": audio_payload(wav)})


# =====================================================================
# Conversation history
# =====================================================================
@app.get("/api/conversations")
def api_list_conversations():
    return jsonify({"success": True, "conversations": database.list_conversations(g.client_id)})


@app.post("/api/conversations")
def api_new_conversation():
    lang = parse_language(get_json_body().get("language"))
    conversation = database.create_conversation(g.client_id, lang)
    return jsonify({"success": True, "conversation": conversation}), 201


@app.get("/api/conversations/<int:conv_id>")
def api_get_conversation(conv_id):
    conversation = database.get_conversation(g.client_id, conv_id)
    if conversation is None:
        raise ApiError("Conversation not found.", "not_found", 404)
    return jsonify({
        "success": True,
        "conversation": conversation,
        "messages": database.get_messages(g.client_id, conv_id),
    })


@app.delete("/api/conversations/<int:conv_id>")
def api_delete_conversation(conv_id):
    if not database.delete_conversation(g.client_id, conv_id):
        raise ApiError("Conversation not found.", "not_found", 404)
    return jsonify({"success": True})


@app.get("/api/stats")
def api_stats():
    return jsonify({"success": True, "stats": database.get_stats(g.client_id)})


# =====================================================================
# Recent voice sessions: your saved voice questions
# =====================================================================
@app.get("/api/recordings")
def api_list_recordings():
    return jsonify({"success": True, "recordings": database.list_recordings(g.client_id)})


@app.get("/api/recordings/<int:rec_id>/audio")
def api_recording_audio(rec_id):
    audio = database.get_recording_audio(g.client_id, rec_id)
    if audio is None:
        raise ApiError("Recording not found.", "not_found", 404)
    return Response(audio, mimetype="audio/wav")


@app.delete("/api/recordings/<int:rec_id>")
def api_delete_recording(rec_id):
    if not database.delete_recording(g.client_id, rec_id):
        raise ApiError("Recording not found.", "not_found", 404)
    return jsonify({"success": True})


# =====================================================================
# Run the development server on your computer:  python app.py
# (On Render, gunicorn starts the app instead.)
# =====================================================================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)