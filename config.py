# import os
# from dotenv import load_dotenv

# # Read the .env file once, here. Every other file imports from this module.
# load_dotenv()

# BASE_DIR = os.path.abspath(os.path.dirname(__file__))


# class Config:
#     # ---------- Flask ----------
#     SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

#     # ---------- Gnani credentials and endpoints ----------
#     GNANI_API_KEY = os.getenv("GNANI_API_KEY")
#     GNANI_STT_URL = os.getenv("GNANI_STT_URL", "https://api.vachana.ai/stt/v3")
#     GNANI_TTS_URL = os.getenv(
#         "GNANI_TTS_URL", "https://api.vachana.ai/api/v1/tts/inference"
#     )

#     # ---------- Models (verified in Phase 2) ----------
#     STT_MODEL = "prisma-v3.0"            # main speech-to-text model
#     STT_FALLBACK_MODEL = "prisma-v2.5"   # used if v3.0 fails
#     TTS_MODEL = "timbre-v2.5"

#     # ---------- Network ----------
#     REQUEST_TIMEOUT = 60                 # seconds before we give up on an API call

#     # ---------- Audio upload rules ----------
#     UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
#     MAX_CONTENT_LENGTH = 5 * 1024 * 1024   # 5 MB: Flask rejects bigger requests
#     ALLOWED_AUDIO_EXTENSIONS = {"wav", "mp3", "ogg", "flac", "aac", "m4a"}
#     MAX_AUDIO_SECONDS = 30               # Gnani REST limit per request
#     MIN_AUDIO_SECONDS = 0.3              # shorter clips are treated as empty

#     # ---------- Database ----------
#     DATABASE_PATH = os.path.join(BASE_DIR, "voicemate.db")

#     # ---------- Languages ----------
#     # key = what the frontend sends. Every language here passed our Phase 2 test.
#     LANGUAGES = {
#         "en": {
#             "label": "English",
#             "native": "English",
#             "stt_code": "en-IN",
#             "tts_code": "en-IN",
#             "tts_voice": "Kaveri",
#             "llm_name": "English",
#         },
#         "hi": {
#             "label": "Hindi",
#             "native": "हिन्दी",
#             "stt_code": "hi-IN",
#             "tts_code": "hi-IN",
#             "tts_voice": "Nalini",
#             "llm_name": "Hindi",
#         },
#         "gu": {
#             "label": "Gujarati",
#             "native": "ગુજરાતી",
#             "stt_code": "gu-IN",
#             "tts_code": "gu-IN",
#             "tts_voice": "Falak",
#             "llm_name": "Gujarati",
#         },
#     }
#     DEFAULT_LANGUAGE = "en"


# def validate_config():
#     """Stop early with a clear message if something important is missing."""
#     problems = []
#     if not Config.GNANI_API_KEY or Config.GNANI_API_KEY == "paste_your_real_key_here":
#         problems.append("GNANI_API_KEY is missing in the .env file.")
#     if Config.SECRET_KEY == "dev-only-change-me":
#         print("WARNING: using the default SECRET_KEY. Set a real one before deploying.")
#     if problems:
#         raise RuntimeError(" ".join(problems))


# def get_language(lang_key):
#     """Return the settings for a language, or None if it is unsupported."""
#     return Config.LANGUAGES.get(lang_key)
import os
from dotenv import load_dotenv

# Read the .env file once, here. Every other file imports from this module.
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # ---------- Flask ----------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

    # ---------- Gnani credentials and endpoints ----------
    GNANI_API_KEY = os.getenv("GNANI_API_KEY")
    GNANI_STT_URL = os.getenv("GNANI_STT_URL", "https://api.vachana.ai/stt/v3")
    GNANI_TTS_URL = os.getenv(
        "GNANI_TTS_URL", "https://api.vachana.ai/api/v1/tts/inference"
    )

    # ---------- Models (verified in Phase 2) ----------
    STT_MODEL = "prisma-v3.0"            # main speech-to-text model
    STT_FALLBACK_MODEL = "prisma-v2.5"   # used if v3.0 fails
    TTS_MODEL = "timbre-v2.5"

    # ---------- LLM (Sarvam, verified in Phase 4) ----------
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_API_URL = os.getenv("LLM_API_URL", "https://api.sarvam.ai/v1/chat/completions")
    LLM_MODEL = os.getenv("LLM_MODEL", "sarvam-105b-conversations")
    LLM_TEMPERATURE = 0.4
    LLM_MAX_TOKENS = 500          # keeps answers short and cheap
    MAX_HISTORY_MESSAGES = 10     # how many earlier messages we send for context
    MAX_QUESTION_CHARS = 1000     # longest question we accept

    # ---------- Network ----------
    REQUEST_TIMEOUT = 60                 # seconds before we give up on an API call

    # ---------- Audio upload rules ----------
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024   # 5 MB: Flask rejects bigger requests
    ALLOWED_AUDIO_EXTENSIONS = {"wav", "mp3", "ogg", "flac", "aac", "m4a"}
    MAX_AUDIO_SECONDS = 30               # Gnani REST limit per request
    MIN_AUDIO_SECONDS = 0.3              # shorter clips are treated as empty

    # ---------- Database ----------
    DATABASE_PATH = os.path.join(BASE_DIR, "voicemate.db")

    # ---------- Languages ----------
    # key = what the frontend sends. Every language here passed our Phase 2 test.
    LANGUAGES = {
        "en": {
            "label": "English",
            "native": "English",
            "stt_code": "en-IN",
            "tts_code": "en-IN",
            "tts_voice": "Kaveri",
            "llm_name": "English",
        },
        "hi": {
            "label": "Hindi",
            "native": "हिन्दी",
            "stt_code": "hi-IN",
            "tts_code": "hi-IN",
            "tts_voice": "Nalini",
            "llm_name": "Hindi",
        },
        "gu": {
            "label": "Gujarati",
            "native": "ગુજરાતી",
            "stt_code": "gu-IN",
            "tts_code": "gu-IN",
            "tts_voice": "Falak",
            "llm_name": "Gujarati",
        },
    }
    DEFAULT_LANGUAGE = "en"


def validate_config():
    """Stop early with a clear message if something important is missing."""
    problems = []
    if not Config.GNANI_API_KEY or Config.GNANI_API_KEY == "paste_your_real_key_here":
        problems.append("GNANI_API_KEY is missing in the .env file.")
    if not Config.LLM_API_KEY or "paste_your" in Config.LLM_API_KEY:
        problems.append("LLM_API_KEY is missing in the .env file.")
    if Config.SECRET_KEY == "dev-only-change-me":
        print("WARNING: using the default SECRET_KEY. Set a real one before deploying.")
    if problems:
        raise RuntimeError(" ".join(problems))


def get_language(lang_key):
    """Return the settings for a language, or None if it is unsupported."""
    return Config.LANGUAGES.get(lang_key)