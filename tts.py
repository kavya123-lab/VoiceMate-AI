import logging
import struct
import time

import requests

from config import Config, get_language

logger = logging.getLogger(__name__)

# 16 kHz mono 16-bit WAV: this exact setting was tested in Phase 2 and works with Prisma too.
AUDIO_CONFIG = {
    "sample_rate": 16000,
    "num_channels": 1,
    "sample_width": 2,
    "encoding": "linear_pcm",
    "container": "wav",
}

# Our own safety limit (not a number from Gnani's docs). Answers are already short.
MAX_TTS_CHARS = 1500


class TTSError(Exception):
    """An error with a friendly message that is safe to show to the user."""

    def __init__(self, message, code="tts_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def fix_wav_header(audio):
    """
    Timbre streams audio, so its WAV header contains a placeholder length.
    We rewrite the two size fields with the real values so every player is happy.
    """
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio  # not a WAV we understand: return it untouched

    marker = audio.find(b"data", 12)
    if marker == -1:
        return audio

    data_start = marker + 8                      # 4 bytes "data" + 4 bytes size
    data_size = len(audio) - data_start
    riff_size = len(audio) - 8

    return (
        audio[:4]
        + struct.pack("<I", riff_size)           # real total size
        + audio[8:marker + 4]                    # "WAVE", format chunk, "data"
        + struct.pack("<I", data_size)           # real audio size
        + audio[data_start:]                     # the audio itself
    )


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


def _post_to_timbre(payload):
    """One request, with one retry for temporary problems. Returns the audio bytes."""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key-ID": Config.GNANI_API_KEY,
    }

    last_error = None
    for attempt in (1, 2):
        try:
            response = requests.post(
                Config.GNANI_TTS_URL,
                headers=headers,
                json=payload,
                timeout=Config.REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            last_error = TTSError("Voice generation took too long.", "timeout")
        except requests.ConnectionError:
            last_error = TTSError("Could not reach the voice service. Check your internet.", "connection")
        else:
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if not response.content or "json" in content_type.lower():
                    logger.error("Timbre returned 200 but no audio: %s", response.text[:200])
                    raise TTSError("The voice service did not return any audio.", "no_audio")
                return response.content

            detail = _extract_error_message(response)
            logger.error("Timbre returned %s: %s", response.status_code, detail)
            code = response.status_code

            if code in (401, 403):
                raise TTSError("The voice service rejected the API key or the credits are used up.", "auth")
            if code == 400:
                raise TTSError("The voice service could not read this text.", "bad_request")
            if code == 429:
                last_error = TTSError("The voice service is busy. Please try again in a moment.", "rate_limit")
            elif code >= 500:
                last_error = TTSError("The voice service is having problems.", "server_error")
            else:
                raise TTSError(f"Unexpected voice service error ({code}).", "unexpected")

        # Only temporary problems reach this point: wait briefly, then retry once
        if attempt == 1:
            logger.warning("Temporary Timbre problem (%s). Retrying once...", last_error.code)
            time.sleep(1.5)

    raise last_error


def synthesize_speech(text, lang_key):
    """
    Turn text into speech.
    Returns: WAV audio as bytes.
    Raises : TTSError (with a friendly message) if anything goes wrong.
    """
    language = get_language(lang_key)
    if language is None:
        raise TTSError(f"Language '{lang_key}' is not supported yet.", "unsupported_language")

    text = (text or "").strip()
    if not text:
        raise TTSError("There is no text to speak.", "empty_text")
    if len(text) > MAX_TTS_CHARS:
        # Cut at the last sentence end before the limit so the voice doesn't stop mid-word
        cut = text[:MAX_TTS_CHARS]
        for stop in ("।", ".", "?", "!"):
            pos = cut.rfind(stop)
            if pos > MAX_TTS_CHARS // 2:
                cut = cut[: pos + 1]
                break
        text = cut

    payload = {
        "text": text,
        "voice": language["tts_voice"],
        "model": Config.TTS_MODEL,
        "language": language["tts_code"],
        "speed": 1.0,
        "audio_config": AUDIO_CONFIG,
    }

    audio = _post_to_timbre(payload)
    return fix_wav_header(audio)


# ---------- Manual test: python tts.py <language_key> ["your text"] ----------
if __name__ == "__main__":
    import sys
    import wave

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)

    SAMPLES = {
        "en": "Photosynthesis is how plants make food from sunlight, water and air.",
        "hi": "प्रकाश संश्लेषण वह प्रक्रिया है जिससे पौधे सूरज की रोशनी से अपना भोजन बनाते हैं।",
        "gu": "પ્રકાશસંશ્લેષણ એ છોડની સૂર્યપ્રકાશથી ખોરાક બનાવવાની રીત છે.",
    }

    if len(sys.argv) < 2:
        raise SystemExit('Usage: python tts.py <language_key: en|hi|gu> ["optional text"]')

    lang = sys.argv[1]
    sample_text = sys.argv[2] if len(sys.argv) > 2 else SAMPLES.get(lang, "")

    try:
        wav_bytes = synthesize_speech(sample_text, lang)
    except TTSError as e:
        raise SystemExit(f"TTS ERROR [{e.code}]: {e.message}")

    out_file = f"test_speech_{lang}.wav"
    with open(out_file, "wb") as f:
        f.write(wav_bytes)

    with wave.open(out_file, "rb") as w:
        seconds = w.getnframes() / float(w.getframerate())
    print(f"SUCCESS: saved {out_file} | {len(wav_bytes)} bytes | {seconds:.1f} seconds")