import logging
import re
import time

import requests

from config import Config, get_language

logger = logging.getLogger(__name__)


class AIError(Exception):
    """An error with a friendly message that is safe to show to the user."""

    def __init__(self, message, code="ai_error"):
        super().__init__(message)
        self.message = message
        self.code = code


# The script each language must be written in
SCRIPT_NAMES = {
    "en": "the English alphabet",
    "hi": "Devanagari script",
    "gu": "Gujarati script",
}

# Unicode ranges used to check which script an answer is written in
DEVANAGARI = (0x0900, 0x097F)
GUJARATI = (0x0A80, 0x0AFF)
ALL_INDIC = (0x0900, 0x0AFF)

SYSTEM_PROMPT_TEMPLATE = """You are VoiceMate AI, a friendly and knowledgeable voice assistant that helps students and curious people.

Language rule (very important): The user chose {language} in the app. Always reply in {language}, written in {script}. This never changes.
The user's message may be in a different language, or in another language typed with English letters (for example Gujarati or Hindi typed in English letters, like "Kem cho" or "Namaste, kaise ho"). Understand it, but still reply only in {language}.

Style rules (your answer will be read aloud by a speech engine):
- Keep answers short: 2 to 4 sentences, unless the user asks for more detail.
- Use plain text only. No markdown, no bullet points, no numbered lists, no headings, no emojis, no asterisks.
- Explain in simple, beginner-friendly words. Use one small everyday example when it helps.
- Be factually accurate. Explain directly in plain words. Use a comparison only if it is exactly correct, and never compare one physical force to another (for example, never compare gravity to a magnet).
- Use the earlier messages to understand follow-up questions such as "why?" or "explain more".
- If you are not sure about something, say so honestly instead of guessing.
- If the question is unclear, ask one short clarifying question.
"""


def build_messages(user_text, history, language_name, script_name):
    """Assemble: system prompt + recent history + the new question (with a language reminder)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(language=language_name, script=script_name)}
    ]

    # Keep only valid, recent messages. History comes from our own database.
    clean_history = [
        {"role": m["role"], "content": m["content"]}
        for m in (history or [])
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    messages.extend(clean_history[-Config.MAX_HISTORY_MESSAGES:])

    # The reminder is sent to the AI only. It is never saved or shown to the user.
    reminder = f"\n\n(Reply only in {language_name}, written in {script_name}.)"
    messages.append({"role": "user", "content": user_text + reminder})
    return messages


def clean_for_speech(text):
    """Remove markdown symbols so the text-to-speech voice doesn't read them out."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)        # code blocks
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)   # bullet points
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE) # numbered lists
    text = re.sub(r"[*_`#>]+", "", text)                           # leftover markdown symbols
    text = re.sub(r"\s+", " ", text)                               # newlines/extra spaces
    return text.strip()


def answer_matches_language(text, lang_key):
    """Check the letters of the answer: is it written in the selected language's script?"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True

    def share(low_high):
        low, high = low_high
        return sum(1 for c in letters if low <= ord(c) <= high) / len(letters)

    if lang_key == "en":
        return share(ALL_INDIC) < 0.1        # almost no Hindi/Gujarati letters
    if lang_key == "hi":
        return share(DEVANAGARI) > 0.5
    if lang_key == "gu":
        return share(GUJARATI) > 0.5
    return True


def _extract_error_message(response):
    """Read an error message from the reply without assuming its exact shape."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
        if isinstance(err, str):
            return err
        if isinstance(data.get("message"), str):
            return data["message"]
    return str(data)[:200]


def _post_to_llm(payload):
    """One request, with one retry for temporary problems. Returns the parsed JSON."""
    headers = {
        "api-subscription-key": Config.LLM_API_KEY,
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in (1, 2):
        try:
            response = requests.post(
                Config.LLM_API_URL,
                headers=headers,
                json=payload,
                timeout=Config.REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            last_error = AIError("The AI took too long to answer. Please try again.", "timeout")
        except requests.ConnectionError:
            last_error = AIError("Could not reach the AI service. Check your internet.", "connection")
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise AIError("The AI service sent an unreadable reply.", "bad_reply")

            detail = _extract_error_message(response)
            logger.error("LLM returned %s: %s", response.status_code, detail)
            code = response.status_code

            if code in (401, 403):
                raise AIError("The AI service rejected the API key.", "auth")
            if code == 402:
                raise AIError("The AI service credits are used up.", "no_credits")
            if code == 400:
                raise AIError("The AI service could not process this request.", "bad_request")
            if code == 429:
                last_error = AIError("The AI is busy right now. Please try again in a moment.", "rate_limit")
            elif code >= 500:
                last_error = AIError("The AI service is having problems.", "server_error")
            else:
                raise AIError(f"Unexpected AI service error ({code}).", "unexpected")

        # Only temporary problems reach this point: wait briefly, then retry once
        if attempt == 1:
            logger.warning("Temporary LLM problem (%s). Retrying once...", last_error.code)
            time.sleep(1.5)

    raise last_error


def _ask_llm(messages):
    """Send the messages, return the cleaned answer text and the token usage."""
    payload = {
        "model": Config.LLM_MODEL,
        "messages": messages,
        "temperature": Config.LLM_TEMPERATURE,
        "max_tokens": Config.LLM_MAX_TOKENS,
        "reasoning_effort": None,   # JSON null: reasoning off = faster and cheaper
    }
    data = _post_to_llm(payload)

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("The AI service sent an unexpected reply.", "bad_reply")

    answer = clean_for_speech(content or "")
    if not answer:
        raise AIError("The AI did not produce an answer. Please try again.", "empty_answer")
    return answer, data.get("usage")


def generate_answer(user_text, history, lang_key):
    """
    Create an answer to the user's question, always in the selected language.
    user_text : the question (typed or recognised by Prisma)
    history   : earlier messages, e.g. [{"role": "user", "content": "..."}, {"role": "assistant", ...}]
    lang_key  : "en", "hi" or "gu"
    Returns   : {"text": "...", "usage": {...}}
    Raises    : AIError (with a friendly message) if anything goes wrong.
    """
    language = get_language(lang_key)
    if language is None:
        raise AIError(f"Language '{lang_key}' is not supported yet.", "unsupported_language")

    user_text = (user_text or "").strip()
    if not user_text:
        raise AIError("Please type or say a question first.", "empty_question")
    if len(user_text) > Config.MAX_QUESTION_CHARS:
        raise AIError(
            f"That question is too long. Please keep it under {Config.MAX_QUESTION_CHARS} characters.",
            "too_long",
        )

    language_name = language["llm_name"]
    script_name = SCRIPT_NAMES.get(lang_key, language_name)
    messages = build_messages(user_text, history, language_name, script_name)

    answer, usage = _ask_llm(messages)

    # Safety check: is the answer really in the selected language? If not, ask once more.
    if not answer_matches_language(answer, lang_key):
        logger.warning("Answer was not in %s. Asking the AI to rewrite it.", language_name)
        messages.append({"role": "assistant", "content": answer})
        messages.append({
            "role": "user",
            "content": f"That answer was not in {language_name}. Write the same answer again, "
                       f"only in {language_name}, using {script_name}.",
        })
        answer, usage = _ask_llm(messages)

        if not answer_matches_language(answer, lang_key):
            raise AIError(
                f"I couldn’t answer in {language_name} this time. Please ask again.",
                "wrong_language",
            )

    return {"text": answer, "usage": usage}


# ---------- Terminal chat test: python ai_brain.py <language_key> ----------
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python ai_brain.py <language_key: en|hi|gu>")

    lang = sys.argv[1]
    if get_language(lang) is None:
        raise SystemExit(f"Unsupported language '{lang}'.")

    print(f"VoiceMate AI terminal test ({lang}). Type 'exit' to stop.\n")
    chat_history = []

    while True:
        question = input("You: ").strip()
        if question.lower() in ("exit", "quit"):
            break
        try:
            result = generate_answer(question, chat_history, lang)
        except AIError as e:
            print(f"AI ERROR [{e.code}]: {e.message}\n")
            continue

        print("AI :", result["text"], "\n")
        chat_history.append({"role": "user", "content": question})
        chat_history.append({"role": "assistant", "content": result["text"]})