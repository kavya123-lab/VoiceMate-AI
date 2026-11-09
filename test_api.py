import base64
import io
import os
import sys
import wave

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
TIMEOUT = 90
results = {"passed": 0, "failed": 0}


# ---------------------------------------------------------------- helpers
def check(name, ok, detail=""):
    if ok:
        print(f"  PASS  {name}")
        results["passed"] += 1
    else:
        print(f"  FAIL  {name}   {detail}")
        results["failed"] += 1


def body(resp):
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text[:200]}


def call(session, method, path, **kwargs):
    try:
        return session.request(method, BASE + path, timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError:
        raise SystemExit("\nCannot reach the server. Is 'python app.py' running in the other terminal?")


def expect_error(name, resp, status, code):
    data = body(resp)
    got_code = (data.get("error") or {}).get("code")
    check(name, resp.status_code == status and got_code == code,
          f"expected {status}/{code}, got {resp.status_code}/{got_code} {data}")


def silent_wav(seconds, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def valid_audio(audio):
    """True if the audio block from the API is a real, playable WAV of sensible length."""
    if not audio or audio.get("mime") != "audio/wav":
        return False
    try:
        raw = base64.b64decode(audio["base64"])
        with wave.open(io.BytesIO(raw), "rb") as w:
            seconds = w.getnframes() / float(w.getframerate())
        return raw[:4] == b"RIFF" and 0.5 < seconds < 60
    except Exception:
        return False


def chat(session, **fields):
    payload = {"language": "en", "voice_output": False, "input_type": "text"}
    payload.update(fields)
    return call(session, "POST", "/api/chat", json=payload)


# ---------------------------------------------------------------- two simulated users
user_a = requests.Session()   # each Session keeps its own cookie, like a separate browser
user_b = requests.Session()

# ---------------------------------------------------------------- 1. basics
print("\n1. Basic endpoints")
r = call(user_a, "GET", "/api/health")
check("health is ok", r.status_code == 200 and body(r).get("status") == "ok")
r = call(user_a, "GET", "/api/config")
langs = [l["key"] for l in body(r).get("languages", [])]
check("config lists en, hi, gu", langs == ["en", "hi", "gu"], langs)
r = call(user_a, "GET", "/api/nothing")
check("unknown URL returns JSON error", r.status_code == 404 and body(r).get("success") is False, body(r))

# ---------------------------------------------------------------- 2. speech to text
print("\n2. Speech to text (Prisma) with your real recordings")
for lang, filename in (("en", "test_en-IN.wav"), ("hi", "test_hi-IN.wav"), ("gu", "test_gu-IN.wav")):
    if not os.path.exists(filename):
        print(f"  SKIP  {filename} not found (create it with: python test_languages.py)")
        continue
    with open(filename, "rb") as f:
        r = call(user_a, "POST", "/api/transcribe",
                 files={"audio": (filename, f, "audio/wav")}, data={"language": lang})
    data = body(r)
    print(f"        [{lang}] heard: {data.get('text')}")
    check(f"transcribe {lang}", r.status_code == 200 and bool(data.get("text")), data)

# ---------------------------------------------------------------- 3. bad audio input
print("\n3. Bad audio input is rejected politely")
r = call(user_a, "POST", "/api/transcribe",
         files={"audio": ("a.wav", silent_wav(1), "audio/wav")}, data={"language": "fr"})
expect_error("unsupported language", r, 400, "unsupported_language")

r = call(user_a, "POST", "/api/transcribe", data={"language": "en"})
expect_error("no file sent", r, 400, "no_file")

r = call(user_a, "POST", "/api/transcribe",
         files={"audio": ("notes.txt", b"hello", "text/plain")}, data={"language": "en"})
expect_error("wrong file type (.txt)", r, 400, "bad_format")

r = call(user_a, "POST", "/api/transcribe",
         files={"audio": ("fake.wav", b"this is not audio at all", "audio/wav")}, data={"language": "en"})
expect_error("fake audio inside a .wav", r, 400, "invalid_audio")

r = call(user_a, "POST", "/api/transcribe",
         files={"audio": ("empty.wav", b"", "audio/wav")}, data={"language": "en"})
expect_error("empty recording (0 bytes)", r, 400, "empty_audio")

r = call(user_a, "POST", "/api/transcribe",
         files={"audio": ("short.wav", silent_wav(0.05), "audio/wav")}, data={"language": "en"})
expect_error("recording too short", r, 400, "too_short")

# ---------------------------------------------------------------- 4. chat + memory
print("\n4. Chat, follow-up memory and history")
r = chat(user_a, text="What is photosynthesis?", language="en")
data = body(r)
check("english question answered", r.status_code == 200 and bool(data.get("assistant_message", {}).get("content")), data)
conv_en = data.get("conversation", {}).get("id")
print(f"        AI: {data.get('assistant_message', {}).get('content')}")

r = chat(user_a, text="Why is it important for us?", language="en", conversation_id=conv_en)
data = body(r)
check("follow-up stays in the same conversation",
      r.status_code == 200 and data.get("conversation", {}).get("id") == conv_en, data)
print(f"        AI: {data.get('assistant_message', {}).get('content')}")

r = call(user_a, "GET", f"/api/conversations/{conv_en}")
data = body(r)
check("conversation has 4 saved messages", len(data.get("messages", [])) == 4, data)

# ---------------------------------------------------------------- 5. languages + voice output
print("\n5. Hindi with voice output, Gujarati text")
r = chat(user_a, text="गुरुत्वाकर्षण क्या है?", language="hi", voice_output=True, input_type="voice")
data = body(r)
check("hindi answer received", r.status_code == 200 and bool(data.get("assistant_message", {}).get("content")), data)
check("hindi audio is a valid WAV", valid_audio(data.get("audio")), data.get("audio_error"))
print(f"        AI: {data.get('assistant_message', {}).get('content')}")

r = chat(user_a, text="પૃથ્વી પર ગુરુત્વાકર્ષણ કેમ છે?", language="gu")
data = body(r)
check("gujarati answer received", r.status_code == 200 and bool(data.get("assistant_message", {}).get("content")), data)
print(f"        AI: {data.get('assistant_message', {}).get('content')}")
conv_gu = data.get("conversation", {}).get("id")
gu_answer_id = data.get("assistant_message", {}).get("id")
gu_question_id = data.get("user_message", {}).get("id")

# ---------------------------------------------------------------- 6. listen button
print("\n6. 'Listen' button")
r = call(user_a, "POST", f"/api/conversations/{conv_gu}/messages/{gu_answer_id}/speech")
check("speak a saved answer", r.status_code == 200 and valid_audio(body(r).get("audio")), body(r))
r = call(user_a, "POST", f"/api/conversations/{conv_gu}/messages/{gu_question_id}/speech")
expect_error("refuses to speak a user message", r, 404, "not_found")

# ---------------------------------------------------------------- 7. chat errors
print("\n7. Chat errors")
expect_error("empty question", chat(user_a, text="   "), 400, "empty_question")
expect_error("question too long", chat(user_a, text="a" * 1001), 400, "too_long")
expect_error("unsupported language", chat(user_a, text="hello", language="fr"), 400, "unsupported_language")
expect_error("unknown conversation", chat(user_a, text="hello", conversation_id=999999), 404, "not_found")

# ---------------------------------------------------------------- 8. history + stats
print("\n8. History and stats")
r = call(user_a, "GET", "/api/conversations")
listing = body(r).get("conversations", [])
check("history lists 3 conversations", len(listing) == 3, f"found {len(listing)}")
r = call(user_a, "GET", "/api/stats")
stats = body(r).get("stats", {})
check("stats are real", stats == {"conversations": 3, "questions": 4, "voice_questions": 1}, stats)

# ---------------------------------------------------------------- 9. privacy
print("\n9. Privacy: a second user must see nothing of the first")
r = call(user_b, "GET", "/api/conversations")
check("user B history is empty", body(r).get("conversations") == [], body(r))
expect_error("user B cannot read A's conversation", call(user_b, "GET", f"/api/conversations/{conv_en}"), 404, "not_found")
expect_error("user B cannot delete A's conversation", call(user_b, "DELETE", f"/api/conversations/{conv_en}"), 404, "not_found")
expect_error("user B cannot chat in A's conversation", chat(user_b, text="hello", conversation_id=conv_en), 404, "not_found")
expect_error("user B cannot hear A's answers",
             call(user_b, "POST", f"/api/conversations/{conv_gu}/messages/{gu_answer_id}/speech"), 404, "not_found")

# ---------------------------------------------------------------- 10. new + delete
print("\n10. New conversation and delete")
r = call(user_a, "POST", "/api/conversations", json={"language": "en"})
new_id = body(r).get("conversation", {}).get("id")
check("new conversation created (201)", r.status_code == 201 and new_id is not None, body(r))
r = call(user_a, "DELETE", f"/api/conversations/{new_id}")
check("conversation deleted", r.status_code == 200 and body(r).get("success") is True, body(r))
expect_error("deleted conversation is gone", call(user_a, "GET", f"/api/conversations/{new_id}"), 404, "not_found")

# ---------------------------------------------------------------- summary
total = results["passed"] + results["failed"]
print("\n" + "=" * 50)
print(f"RESULT: {results['passed']} of {total} checks passed")
if results["failed"]:
    print("Some checks FAILED. Paste this output and the server terminal's output to me.")
    sys.exit(1)
print("ALL API TESTS PASSED")