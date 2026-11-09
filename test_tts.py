import os
import requests
from dotenv import load_dotenv

# 1. Load the secrets from the .env file into environment variables
load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
TTS_URL = os.getenv("GNANI_TTS_URL")

if not API_KEY or API_KEY == "paste_your_real_key_here":
    raise SystemExit("ERROR: Put your real key in the .env file first.")

# 2. The headers carry our authentication
headers = {
    "Content-Type": "application/json",
    "X-API-Key-ID": API_KEY,
}

# 3. The request body (field names are from the official docs)
payload = {
    "text": "Hello! I am VoiceMate AI. How can I help you today?",
    "voice": "Kaveri",          # an English voice from the timbre-v2.5 catalog
    "model": "timbre-v2.5",
    "language": "en-IN",
    "speed": 1.0,
    "audio_config": {
        "sample_rate": 16000,   # 16 kHz so Prisma can read this file later
        "num_channels": 1,
        "sample_width": 2,
        "encoding": "linear_pcm",
        "container": "wav",
    },
}

# 4. Send the request (timeout stops it from hanging forever)
print("Calling Timbre...")
response = requests.post(TTS_URL, headers=headers, json=payload, timeout=60)

print("Status code:", response.status_code)
print("Content-Type:", response.headers.get("Content-Type"))

# 5. Success means binary audio. Anything else is an error message in JSON.
if response.status_code == 200:
    with open("test_output.wav", "wb") as f:
        f.write(response.content)
    print(f"Saved test_output.wav ({len(response.content)} bytes)")
else:
    print("FAILED. Response from Gnani:")
    print(response.text)