import logging
import os
import wave

import requests

from config import Config, get_language

logger = logging.getLogger(__name__)

# Gnani accepts 8 kHz to 44.1 kHz audio only
MAX_SAMPLE_RATE = 44100
MIN_SAMPLE_RATE = 8000

MIME_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
}


class STTError(Exception):
    """An error with a friendly message that is safe to show to the user."""

    def __init__(self, message, code="stt_error", fallback_ok=False):
        super().__init__(message)
        self.message = message
        self.code = code
        # True when trying the backup model might help (server trouble, timeout)
        self.fallback_ok = fallback_ok

def validate_audio(file_path):
    """Check the file BEFORE spending credits on it. Raises STTError if bad."""
    if not os.path.isfile(file_path):
        raise STTError("The audio file could not be found.", "no_file")

    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext not in Config.ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(Config.ALLOWED_AUDIO_EXTENSIONS))
        raise STTError(f"Unsupported audio type '.{ext}'. Allowed: {allowed}.", "bad_format")

    size = os.path.getsize(file_path)
    if size == 0:
        raise STTError("The recording is empty. Please try again.", "empty_audio")
    if size > Config.MAX_CONTENT_LENGTH:
        raise STTError("The audio file is too large.", "too_large")

    # We can measure duration and sample rate for WAV files with Python's built-in module
    if ext == "wav":
        try:
            with wave.open(file_path, "rb") as w:
                rate = w.getframerate()
                frame_bytes = w.getnchannels() * w.getsampwidth()
                header_frames = w.getnframes()
        except (wave.Error, EOFError):
            raise STTError("This is not a valid audio recording.", "invalid_audio")

        if not rate or not frame_bytes:
            raise STTError("This is not a valid audio recording.", "invalid_audio")

        # Some WAV files (including ones from streaming servers) carry a wrong
        # length in the header, so we also measure from the real file size.
        # The smaller number is the trustworthy one.
        real_frames = size // frame_bytes
        duration = min(header_frames, real_frames) / float(rate)

        if duration < Config.MIN_AUDIO_SECONDS:
            raise STTError("The recording is too short. Please speak a little longer.", "too_short")
        if duration > Config.MAX_AUDIO_SECONDS:
            raise STTError(
                f"The recording is longer than {Config.MAX_AUDIO_SECONDS} seconds. "
                "Please ask a shorter question.",
                "too_long",
            )
        if not (MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE):
            raise STTError(f"Unsupported audio sample rate: {rate} Hz.", "bad_sample_rate")
def _extract_error_message(response):
    """Read an error message from Gnani's reply without assuming its exact shape."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
        for key in ("message", "detail", "error"):
            if isinstance(data.get(key), str):
                return data[key]
    return str(data)[:200]


def _call_prisma(file_path, model, language_code):
    """Send one request to Prisma. Returns the parsed JSON or raises STTError."""
    form = {"language_code": language_code}
    # Prisma v2.5 is Gnani's default model, so we only send `model` for v3.0
    if model == Config.STT_MODEL:
        form["model"] = model

    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    mime = MIME_TYPES.get(ext, "application/octet-stream")

    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                Config.GNANI_STT_URL,
                headers={"X-API-Key-ID": Config.GNANI_API_KEY},
                files={"audio_file": (os.path.basename(file_path), f, mime)},
                data=form,
                timeout=Config.REQUEST_TIMEOUT,
            )
    except requests.Timeout:
        raise STTError("Speech recognition took too long. Please try again.", "timeout", fallback_ok=True)
    except requests.ConnectionError:
        raise STTError("Could not reach the speech service. Check your internet.", "connection", fallback_ok=True)

    if response.status_code == 200:
        try:
            return response.json()
        except ValueError:
            raise STTError("The speech service sent an unreadable reply.", "bad_reply", fallback_ok=True)

    detail = _extract_error_message(response)
    logger.error("Prisma %s returned %s: %s", model, response.status_code, detail)

    if response.status_code in (401, 403):
        raise STTError("The speech service rejected the API key or the credits are used up.", "auth")
    if response.status_code == 429:
        raise STTError("Too many requests right now. Please wait a moment and try again.", "rate_limit")
    if response.status_code == 400:
        raise STTError("The speech service could not process this audio.", "bad_request")
    if response.status_code >= 500:
        raise STTError("The speech service is having problems.", "server_error", fallback_ok=True)
    raise STTError(f"Unexpected speech service error ({response.status_code}).", "unexpected")


def transcribe_audio(file_path, lang_key):
    """
    Turn an audio file into text.
    Returns: {"text": ..., "language": ..., "detected_language": ..., "model": ...}
    Raises:  STTError (with a friendly message) if anything goes wrong.
    """
    language = get_language(lang_key)
    if language is None:
        raise STTError(f"Language '{lang_key}' is not supported yet.", "unsupported_language")

    validate_audio(file_path)
    code = language["stt_code"]

    try:
        result = _call_prisma(file_path, Config.STT_MODEL, code)
        used_model = Config.STT_MODEL
    except STTError as err:
        if not err.fallback_ok:
            raise
        logger.warning("Prisma v3.0 failed (%s). Trying fallback model.", err.code)
        result = _call_prisma(file_path, Config.STT_FALLBACK_MODEL, code)
        used_model = Config.STT_FALLBACK_MODEL

    text = (result.get("transcript") or "").strip()
    if not text:
        raise STTError("I couldn't hear any speech. Please try again.", "no_speech")

    return {
        "text": text,
        "language": lang_key,
        "detected_language": result.get("detected_language_code"),
        "model": result.get("model", used_model),
    }


# ---------- Quick manual test: python stt.py <audio_file> <language_key> ----------
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 3:
        raise SystemExit("Usage: python stt.py <audio_file> <language_key: en|hi|gu>")

    try:
        out = transcribe_audio(sys.argv[1], sys.argv[2])
        print("SUCCESS:", out)
    except STTError as e:
        print(f"STT ERROR [{e.code}]: {e.message}")