import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
STT_URL = os.getenv("GNANI_STT_URL")
AUDIO_FILE = "test_output.wav"   # created by test_tts.py

if not API_KEY or not STT_URL:
    raise SystemExit("ERROR: GNANI_API_KEY or GNANI_STT_URL missing in .env")

if not os.path.exists(AUDIO_FILE):
    raise SystemExit(f"ERROR: {AUDIO_FILE} not found. Run test_tts.py first.")

# Only the API key goes in headers.
# Do NOT set Content-Type here: requests sets it for file uploads automatically.
headers = {"X-API-Key-ID": API_KEY}


def transcribe(form_fields):
    """Send the audio file to Prisma and print the result."""
    with open(AUDIO_FILE, "rb") as f:
        files = {"audio_file": (AUDIO_FILE, f, "audio/wav")}
        response = requests.post(
            STT_URL,
            headers=headers,
            files=files,
            data=form_fields,
            timeout=60,
        )

    print("Status code:", response.status_code)
    try:
        print("Response JSON:", response.json())
    except ValueError:
        print("Response was not JSON:", response.text[:300])
    print("-" * 50)


# Test 1: Prisma v2.5 (the default model), English chosen manually
print("TEST 1: Prisma v2.5, language_code=en-IN")
transcribe({"language_code": "en-IN"})

# Test 2: Prisma v3.0 with automatic language detection
print("TEST 2: Prisma v3.0, language_code=indic-auto")
transcribe({"model": "prisma-v3.0", "language_code": "indic-auto"})