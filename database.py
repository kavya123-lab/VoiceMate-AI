import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import Config

DEFAULT_TITLE = "New conversation"
TITLE_MAX_CHARS = 40
MAX_RECORDINGS_PER_CLIENT = 20   # only the newest voice recordings are kept


class ConversationNotFound(Exception):
    """Raised when a conversation does not exist or belongs to another client."""


def _now():
    """Current UTC time as text, e.g. 2026-11-07T12:30:00+00:00"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_db():
    """Open a connection, save changes if all went well, undo them if not, always close."""
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row          # lets us read columns by name
    conn.execute("PRAGMA foreign_keys = ON")  # makes deleting a conversation delete its messages
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create the tables if they do not exist yet. Safe to call on every start."""
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  TEXT    NOT NULL,
                title      TEXT    NOT NULL DEFAULT 'New conversation',
                language   TEXT    NOT NULL DEFAULT 'en',
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role            TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
                content         TEXT    NOT NULL,
                language        TEXT    NOT NULL DEFAULT 'en',
                input_type      TEXT    NOT NULL DEFAULT 'text' CHECK (input_type IN ('text', 'voice')),
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recordings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       TEXT    NOT NULL,
                conversation_id INTEGER,
                message_id      INTEGER,
                audio           BLOB    NOT NULL,
                duration        REAL    NOT NULL DEFAULT 0,
                language        TEXT    NOT NULL DEFAULT 'en',
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_client
                ON conversations (client_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages (conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_recordings_client
                ON recordings (client_id, id);
            """
        )


def create_conversation(client_id, language="en"):
    """Start a new, empty conversation for this client."""
    now = _now()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO conversations (client_id, title, language, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (client_id, DEFAULT_TITLE, language, now, now),
        )
        conv_id = cur.lastrowid
    return get_conversation(client_id, conv_id)


def get_conversation(client_id, conv_id):
    """Return one conversation as a dict, or None if it is not this client's."""
    with get_db() as db:
        row = db.execute(
            "SELECT id, title, language, created_at, updated_at FROM conversations "
            "WHERE id = ? AND client_id = ?",
            (conv_id, client_id),
        ).fetchone()
    return dict(row) if row else None


def list_conversations(client_id, limit=50):
    """This client's conversations, newest first, for the history sidebar."""
    with get_db() as db:
        rows = db.execute(
            "SELECT c.id, c.title, c.language, c.created_at, c.updated_at, "
            "       COUNT(m.id) AS message_count "
            "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
            "WHERE c.client_id = ? "
            "GROUP BY c.id "
            "ORDER BY c.updated_at DESC, c.id DESC "
            "LIMIT ?",
            (client_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(client_id, conv_id):
    """All messages of a conversation in order. Raises ConversationNotFound if not owned."""
    if get_conversation(client_id, conv_id) is None:
        raise ConversationNotFound()
    with get_db() as db:
        rows = db.execute(
            "SELECT id, role, content, language, input_type, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(client_id, conv_id, role, content, language="en", input_type="text"):
    """Save one message. The first user message also becomes the conversation title."""
    if get_conversation(client_id, conv_id) is None:
        raise ConversationNotFound()

    now = _now()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO messages (conversation_id, role, content, language, input_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, role, content, language, input_type, now),
        )
        message_id = cur.lastrowid

        # Use the first question as the conversation title
        if role == "user":
            title = " ".join(content.split())
            if len(title) > TITLE_MAX_CHARS:
                title = title[:TITLE_MAX_CHARS].rstrip() + "…"
            db.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ?",
                (title, conv_id, DEFAULT_TITLE),
            )

        db.execute(
            "UPDATE conversations SET updated_at = ?, language = ? WHERE id = ?",
            (now, language, conv_id),
        )
    return {"id": message_id, "role": role, "content": content,
            "language": language, "input_type": input_type, "created_at": now}


def get_history_for_llm(client_id, conv_id, limit=None):
    """The last few messages in the exact shape ai_brain.py expects."""
    limit = limit or Config.MAX_HISTORY_MESSAGES
    if get_conversation(client_id, conv_id) is None:
        raise ConversationNotFound()
    with get_db() as db:
        rows = db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def delete_conversation(client_id, conv_id):
    """Delete a conversation, its messages and its voice recordings. True if something was deleted."""
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM conversations WHERE id = ? AND client_id = ?",
            (conv_id, client_id),
        )
        return cur.rowcount > 0


def get_stats(client_id):
    """Real numbers for the dashboard cards."""
    with get_db() as db:
        conversations = db.execute(
            "SELECT COUNT(*) FROM conversations WHERE client_id = ?", (client_id,)
        ).fetchone()[0]
        row = db.execute(
            "SELECT "
            "  SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN m.role = 'user' AND m.input_type = 'voice' THEN 1 ELSE 0 END) "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.client_id = ?",
            (client_id,),
        ).fetchone()
    return {
        "conversations": conversations,
        "questions": row[0] or 0,
        "voice_questions": row[1] or 0,
    }


# =====================================================================
# Voice recordings ("Recent voice sessions")
# Flow: /api/transcribe saves the audio -> /api/chat links it to the question.
# =====================================================================
def save_recording(client_id, audio, duration, language):
    """Store a voice question. Keeps only the newest MAX_RECORDINGS_PER_CLIENT per client."""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO recordings (client_id, audio, duration, language, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (client_id, audio, duration, language, _now()),
        )
        rec_id = cur.lastrowid
        # Remove older recordings that never got attached to a question (for example, the AI failed)
        db.execute(
            "DELETE FROM recordings WHERE client_id = ? AND message_id IS NULL AND id < ?",
            (client_id, rec_id),
        )
        # Keep only the newest ones
        db.execute(
            "DELETE FROM recordings WHERE client_id = ? AND id NOT IN "
            "(SELECT id FROM recordings WHERE client_id = ? ORDER BY id DESC LIMIT ?)",
            (client_id, client_id, MAX_RECORDINGS_PER_CLIENT),
        )
    return rec_id


def attach_recording(client_id, recording_id, conv_id, message_id):
    """Link a saved recording to the question it was transcribed into."""
    with get_db() as db:
        db.execute(
            "UPDATE recordings SET conversation_id = ?, message_id = ? "
            "WHERE id = ? AND client_id = ? AND message_id IS NULL",
            (conv_id, message_id, recording_id, client_id),
        )


def list_recordings(client_id, limit=MAX_RECORDINGS_PER_CLIENT):
    """Recent voice sessions for the dashboard, newest first. Only recordings linked to a question."""
    with get_db() as db:
        rows = db.execute(
            "SELECT r.id, r.conversation_id, r.duration, r.language, r.created_at, m.content AS text "
            "FROM recordings r JOIN messages m ON m.id = r.message_id "
            "WHERE r.client_id = ? ORDER BY r.id DESC LIMIT ?",
            (client_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recording_audio(client_id, rec_id):
    """The WAV bytes of one recording, or None if it is not this client's (or not linked yet)."""
    with get_db() as db:
        row = db.execute(
            "SELECT audio FROM recordings WHERE id = ? AND client_id = ? AND message_id IS NOT NULL",
            (rec_id, client_id),
        ).fetchone()
    return bytes(row["audio"]) if row else None
def delete_recording(client_id, rec_id):
    """Delete one voice recording (the question text stays in the conversation). True if deleted."""
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM recordings WHERE id = ? AND client_id = ?",
            (rec_id, client_id),
        )
        return cur.rowcount > 0

# ---------- Self-test: python database.py (uses a temporary database, not your real one) ----------
if __name__ == "__main__":
    import os
    import shutil
    import sys
    import tempfile

    sys.stdout.reconfigure(encoding="utf-8")

    tmp_dir = tempfile.mkdtemp()
    Config.DATABASE_PATH = os.path.join(tmp_dir, "test.db")   # only for this test run
    init_db()
    init_db()   # calling it twice must be harmless
    print("1. Database created")

    conv = create_conversation("client-A", "en")
    assert conv["title"] == DEFAULT_TITLE
    print("2. Conversation created:", conv["id"])

    add_message("client-A", conv["id"], "user", "What is photosynthesis? " * 5, "en", "voice")
    add_message("client-A", conv["id"], "assistant", "Plants make food from sunlight.", "en")
    add_message("client-A", conv["id"], "user", "પ્રકાશસંશ્લેષણ શું છે?", "gu", "text")
    print("3. Three messages saved (including Gujarati)")

    conv_after = get_conversation("client-A", conv["id"])
    assert conv_after["title"].startswith("What is photosynthesis?"), conv_after["title"]
    assert conv_after["title"].endswith("…") and len(conv_after["title"]) <= TITLE_MAX_CHARS + 1
    print("4. Title taken from first question:", conv_after["title"])

    messages = get_messages("client-A", conv["id"])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[2]["content"] == "પ્રકાશસંશ્લેષણ શું છે?"
    print("5. Messages read back in order, Gujarati intact")

    history = get_history_for_llm("client-A", conv["id"], limit=2)
    assert [m["role"] for m in history] == ["assistant", "user"]
    print("6. LLM history gives the last 2 messages in order")

    listing = list_conversations("client-A")
    assert len(listing) == 1 and listing[0]["message_count"] == 3
    print("7. Sidebar list OK:", listing[0]["title"], "-", listing[0]["message_count"], "messages")

    stats = get_stats("client-A")
    assert stats == {"conversations": 1, "questions": 2, "voice_questions": 1}, stats
    print("8. Stats OK:", stats)

    # Privacy: another client must not see or touch client-A's data
    assert get_conversation("client-B", conv["id"]) is None
    assert list_conversations("client-B") == []
    try:
        get_messages("client-B", conv["id"])
        raise AssertionError("client-B could read client-A's messages!")
    except ConversationNotFound:
        pass
    assert delete_conversation("client-B", conv["id"]) is False
    print("9. Privacy OK: client-B cannot see or delete client-A's data")

    assert delete_conversation("client-A", conv["id"]) is True
    assert list_conversations("client-A") == []
    assert get_stats("client-A")["questions"] == 0   # messages were deleted too
    print("10. Delete OK (messages deleted with the conversation)")

    # ---------------- Voice recordings ----------------
    conv2 = create_conversation("client-A", "en")
    m1 = add_message("client-A", conv2["id"], "user", "hello voice", "en", "voice")

    fake_wav = b"RIFF-fake-wav-bytes"
    r1 = save_recording("client-A", fake_wav, 2.5, "en")
    assert get_recording_audio("client-A", r1) is None          # not linked to a question yet
    attach_recording("client-A", r1, conv2["id"], m1["id"])
    sessions = list_recordings("client-A")
    assert len(sessions) == 1 and sessions[0]["text"] == "hello voice" and sessions[0]["duration"] == 2.5
    assert get_recording_audio("client-A", r1) == fake_wav
    print("11. Recording saved, linked to its question, and read back")

    # A recording that never got linked is hidden, and removed by the next save
    r2 = save_recording("client-A", b"orphan", 1.0, "en")
    assert get_recording_audio("client-A", r2) is None
    assert len(list_recordings("client-A")) == 1
    r3 = save_recording("client-A", b"next", 1.0, "en")
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM recordings WHERE id = ?", (r2,)).fetchone()[0] == 0
    print("12. Unlinked recordings are hidden and cleaned up")

    # Privacy for recordings
    assert list_recordings("client-B") == []
    assert get_recording_audio("client-B", r1) is None
    attach_recording("client-B", r3, conv2["id"], m1["id"])       # wrong client: must change nothing
    assert get_recording_audio("client-A", r3) is None
    print("13. Privacy OK: client-B cannot hear or link client-A's recordings")

    # Only the newest MAX_RECORDINGS_PER_CLIENT are kept
    for i in range(25):
        msg = add_message("client-A", conv2["id"], "user", f"question {i}", "en", "voice")
        rid = save_recording("client-A", b"x" * 10, 1.0, "en")
        attach_recording("client-A", rid, conv2["id"], msg["id"])
    kept = list_recordings("client-A", limit=100)
    assert len(kept) == MAX_RECORDINGS_PER_CLIENT, len(kept)
    assert kept[0]["text"] == "question 24"                       # newest first
    print(f"14. Limit OK: only the newest {MAX_RECORDINGS_PER_CLIENT} recordings are kept")

    # Deleting a conversation deletes its recordings
    assert delete_conversation("client-A", conv2["id"]) is True
    assert list_recordings("client-A", limit=100) == []
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM recordings WHERE client_id = 'client-A'").fetchone()[0] == 0
    print("15. Delete OK (recordings deleted with the conversation)")
        # Deleting ONE recording keeps the conversation and its messages
    conv3 = create_conversation("client-A", "en")
    m3 = add_message("client-A", conv3["id"], "user", "to delete", "en", "voice")
    r4 = save_recording("client-A", b"bye", 1.0, "en")
    attach_recording("client-A", r4, conv3["id"], m3["id"])
    assert delete_recording("client-B", r4) is False          # another client cannot delete it
    assert get_recording_audio("client-A", r4) == b"bye"
    assert delete_recording("client-A", r4) is True
    assert get_recording_audio("client-A", r4) is None
    assert delete_recording("client-A", r4) is False          # already gone
    assert any(m["content"] == "to delete" for m in get_messages("client-A", conv3["id"]))
    print("16. Delete one recording OK (the question text stays in the conversation)")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\nALL DATABASE TESTS PASSED")