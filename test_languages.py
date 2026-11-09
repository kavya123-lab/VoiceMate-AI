import os
import sys
import requests
from dotenv import load_dotenv

# Make the Windows terminal able to print Hindi/Gujarati text
sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
STT_URL = os.getenv("GNANI_STT_URL")
TTS_URL = os.getenv("GNANI_TTS_URL")

if not (API_KEY and STT_URL and TTS_URL):
    raise SystemExit("ERROR: Check GNANI_API_KEY, GNANI_STT_URL, GNANI_TTS_URL in .env")

# For each language: the code, a Timbre v2.5 voice made for that language, and a test sentence
TESTS = [
    {
        "name": "English",
        "code": "en-IN",
        "voice": "Kaveri",
        "text": "Hello, I am VoiceMate AI. How can I help you today?",
    },
    {
        "name": "Hindi",
        "code": "hi-IN",
        "voice": "Nalini",
        "text": "नमस्ते, मैं वॉइसमेट एआई हूँ। मैं आपकी कैसे मदद कर सकता हूँ?",
    },
    {
        "name": "Gujarati",
        "code": "gu-IN",
        "voice": "Falak",
        "text": "નમસ્તે, હું વોઇસમેટ એઆઈ છું. હું તમારી કેવી રીતે મદદ કરી શકું?",
    },
]


def text_to_speech(text, voice, language, out_path):
    """Call Timbre and save the audio. Returns True on success."""
    payload = {
        "text": text,
        "voice": voice,
        "model": "timbre-v2.5",
        "language": language,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": 16000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }
    r = requests.post(
        TTS_URL,
        headers={"Content-Type": "application/json", "X-API-Key-ID": API_KEY},
        json=payload,
        timeout=60,
    )
    if r.status_code != 200:
        print(f"   TTS FAILED ({r.status_code}): {r.text[:300]}")
        return False
    with open(out_path, "wb") as f:
        f.write(r.content)
    print(f"   TTS OK -> {out_path} ({len(r.content)} bytes)")
    return True


def speech_to_text(path, form_fields):
    """Call Prisma and return the JSON (or an error description)."""
    with open(path, "rb") as f:
        r = requests.post(
            STT_URL,
            headers={"X-API-Key-ID": API_KEY},
            files={"audio_file": (os.path.basename(path), f, "audio/wav")},
            data=form_fields,
            timeout=60,
        )
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    data["_status"] = r.status_code
    return data


for t in TESTS:
    print("=" * 60)
    print(f"{t['name']} ({t['code']})")
    print("   Original  :", t["text"])

    wav_path = f"test_{t['code']}.wav"
    if not text_to_speech(t["text"], t["voice"], t["code"], wav_path):
        continue

    # Prisma v2.5 with the language chosen manually
    r25 = speech_to_text(wav_path, {"language_code": t["code"]})
    print("   v2.5 heard:", r25.get("transcript", r25))

    # Prisma v3.0 with automatic language detection
    r30 = speech_to_text(wav_path, {"model": "prisma-v3.0", "language_code": "indic-auto"})
    print("   v3.0 heard:", r30.get("transcript", r30))
    print("   v3.0 detected language:", r30.get("detected_language_code"))