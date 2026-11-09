import difflib
import sys
import textwrap
import unicodedata

import requests

from ai_brain import build_messages, clean_for_speech
from config import Config

sys.stdout.reconfigure(encoding="utf-8")

# (language key, language name, question, number of runs)
TESTS = [
    ("en", "English", "What is photosynthesis?", 4),
    ("hi", "Hindi", "गुरुत्वाकर्षण क्या है?", 2),
    ("gu", "Gujarati", "પૃથ્વી પર ગુરુત્વાકર્ષણ કેમ છે?", 2),
]
OUTPUT_FILE = "debug_output.txt"


def ask_llm(question, language_name):
    payload = {
        "model": Config.LLM_MODEL,
        "messages": build_messages(question, [], language_name),
        "temperature": Config.LLM_TEMPERATURE,
        "max_tokens": Config.LLM_MAX_TOKENS,
        "reasoning_effort": None,
    }
    r = requests.post(
        Config.LLM_API_URL,
        headers={"api-subscription-key": Config.LLM_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    return r.json()["choices"][0]["message"]["content"]


def odd_characters(text):
    """Invisible or unusual characters: control, format, and non-normal spaces."""
    found = []
    for i, ch in enumerate(text):
        category = unicodedata.category(ch)
        if category in ("Cc", "Cf", "Zl", "Zp") or (category == "Zs" and ch != " "):
            found.append((i, f"U+{ord(ch):04X}", unicodedata.name(ch, "UNKNOWN")))
    return found


def changes_made_by_cleaning(raw, cleaned):
    """List every difference between the model's raw text and the cleaned text."""
    matcher = difflib.SequenceMatcher(None, raw, cleaned, autojunk=False)
    return [
        (tag, raw[i1:i2], cleaned[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def long_english_words(text, limit=16):
    """In English, a very long 'word' usually means two words glued together."""
    words = [w.strip(".,!?;:()\"'") for w in text.split()]
    return [w for w in words if len(w) >= limit]


lines = []
glue_suspects = 0

for lang, name, question, runs in TESTS:
    for run in range(1, runs + 1):
        raw = ask_llm(question, name)
        cleaned = clean_for_speech(raw)
        odd = odd_characters(raw)
        changes = changes_made_by_cleaning(raw, cleaned)

        lines.append("=" * 70)
        lines.append(f"{name} - run {run}")
        lines.append(f"QUESTION  : {question}")
        lines.append(f"RAW       : {raw}")
        lines.append(f"CLEANED   : {cleaned}")
        lines.append(f"WORDS     : {' | '.join(cleaned.split(' '))}")
        lines.append(f"ODD CHARS : {odd or 'none'}")
        lines.append(f"CHANGES MADE BY CLEANING: {changes or 'none'}")

        if lang == "en":
            suspects = long_english_words(cleaned)
            glue_suspects += len(suspects)
            lines.append(f"LONG WORDS (possible glue): {suspects or 'none'}")
            print("=" * 60)
            print(f"English - run {run}")
            print(textwrap.fill(cleaned, width=80))   # wraps at spaces, so nothing gets glued
            print("Odd characters:", odd or "none", "| Long words:", suspects or "none")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("\n" + "=" * 60)
print(f"Full results saved to {OUTPUT_FILE}")
print("Open it in the VS Code editor (not the terminal) to read Hindi and Gujarati properly.")
print(f"English words that look glued: {glue_suspects}")