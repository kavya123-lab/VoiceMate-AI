import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

API_KEY = os.getenv("LLM_API_KEY")
URL = os.getenv("LLM_API_URL")
MODEL = os.getenv("LLM_MODEL")

if not (API_KEY and URL and MODEL) or "paste_your" in API_KEY:
    raise SystemExit("ERROR: Fill LLM_API_KEY, LLM_API_URL and LLM_MODEL in .env first.")

headers = {
    "api-subscription-key": API_KEY,   # header name from Sarvam's docs
    "Content-Type": "application/json",
}

SYSTEM_PROMPT = (
    "You are VoiceMate AI, a friendly voice assistant. "
    "Answer in the same language the user writes in. "
    "Keep answers to 2 or 3 short sentences. Use plain text only, no markdown."
)

QUESTIONS = [
    ("English", "Explain photosynthesis in simple words."),
    ("Hindi", "प्रकाश संश्लेषण क्या है? सरल भाषा में समझाइए।"),
    ("Gujarati", "પ્રકાશસંશ્લેષણ શું છે? સરળ ભાષામાં સમજાવો."),
]

for name, question in QUESTIONS:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
        "reasoning_effort": None,   # becomes JSON null: reasoning off = faster and cheaper
    }

    print("=" * 60)
    print(f"{name}: {question}")

    start = time.time()
    try:
        r = requests.post(URL, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        print("REQUEST FAILED:", e)
        continue
    elapsed = time.time() - start

    print(f"Status: {r.status_code} | Time: {elapsed:.1f}s")
    if r.status_code != 200:
        print("Error body:", r.text[:400])
        continue

    data = r.json()
    choice = data["choices"][0]
    message = choice["message"]
    print("Message keys :", list(message.keys()))
    print("Finish reason:", choice.get("finish_reason"))
    print("Usage        :", data.get("usage"))
    print("ANSWER       :", message.get("content"))