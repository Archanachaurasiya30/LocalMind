"""
Conversation persistence, attachment ownership and recoverable partial replies.

Completed pairs are saved atomically. Interrupted requests stay separate so they
can be displayed after restart without becoming authoritative model history.
"""

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from app.storage import data_dir as _data_dir


# ---------------------------------------------------------------------------
# DB path (reuses the same data dir as memory / knowledge)
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return _data_dir() / "conversations.db"


# ---------------------------------------------------------------------------
# Connection + schema bootstrap
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversation_messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            thinking        TEXT,
            created_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conv_messages_conv_id
            ON conversation_messages(conversation_id, created_at);

        CREATE TABLE IF NOT EXISTS chat_attachments (
            id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
            message_id TEXT REFERENCES conversation_messages(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            data BLOB NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_attachments_message ON chat_attachments(message_id);

        CREATE TABLE IF NOT EXISTS interrupted_turns (
            request_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_content TEXT NOT NULL,
            assistant_content TEXT NOT NULL,
            assistant_thinking TEXT,
            error_message TEXT,
            cancelled INTEGER NOT NULL,
            attachment_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_interrupted_conversation ON interrupted_turns(conversation_id, created_at);
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_messages)")}
    if "sources" not in columns:
        conn.execute("ALTER TABLE conversation_messages ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'")
    conversation_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "project_id" not in conversation_columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_conversation(title: str) -> dict:
    """Create a new conversation. Returns the full row as a dict."""
    now = datetime.now(timezone.utc).isoformat()
    cid = str(uuid.uuid4())
    conn = _get_conn()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (cid, title, now, now),
    )
    conn.commit()
    conn.close()
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


def list_conversations() -> list[dict]:
    """Return all conversations, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, project_id, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, title, project_id FROM conversations WHERE id = ?", (conversation_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_messages(conversation_id: str) -> list[dict]:
    """Return all messages for a conversation in chronological order."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT id, conversation_id, role, content, thinking, created_at, sources
        FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC, rowid ASC
        """,
        (conversation_id,),
    ).fetchall()
    attachments = conn.execute(
        "SELECT id, draft_id, message_id, filename, mime_type, width, height, length(data) AS bytes "
        "FROM chat_attachments WHERE conversation_id = ? ORDER BY created_at, rowid",
        (conversation_id,),
    ).fetchall()
    interrupted = conn.execute("SELECT * FROM interrupted_turns WHERE conversation_id=? ORDER BY created_at, rowid",
                               (conversation_id,)).fetchall()
    conn.close()
    messages = [{**dict(r), "sources": json.loads(r["sources"]), "status": "completed", "partial": False,
             "attachments": [{k: a[k] for k in a.keys() if k not in ("message_id", "draft_id")}
                             for a in attachments if a["message_id"] == r["id"]]} for r in rows]
    for turn in interrupted:
        common = {"conversation_id": conversation_id, "request_id": turn["request_id"],
                  "created_at": turn["created_at"], "status": "interrupted", "partial": True,
                  "sources": []}
        ids = set(json.loads(turn["attachment_ids"]))
        owned = [a for a in attachments if a["id"] in ids]
        draft_ids = {a["draft_id"] for a in owned if a["message_id"] is None}
        retry_draft = next(iter(draft_ids)) if (
            len(draft_ids) == 1 and len(owned) == len(ids)
            and all(a["message_id"] is None for a in owned)
        ) else None
        messages.append({**common, "id": turn["request_id"] + ":user", "role": "user",
                         "content": turn["user_content"], "thinking": None,
                         "attachment_draft_id": retry_draft,
                         "attachments": [{k: a[k] for k in a.keys() if k not in ("message_id", "draft_id")}
                                         for a in owned]})
        messages.append({**common, "id": turn["request_id"], "role": "assistant",
                         "content": turn["assistant_content"], "thinking": turn["assistant_thinking"],
                         "error": not bool(turn["cancelled"]), "error_message": turn["error_message"],
                         "cancelled": bool(turn["cancelled"]), "attachments": []})
    return sorted(messages, key=lambda row: row["created_at"])


def save_interrupted_turn(conversation_id: str, request_id: str, user_content: str,
                          assistant_content: str, assistant_thinking: str | None = None,
                          error: str | None = None, cancelled: bool = False,
                          attachment_ids: list[str] | None = None) -> None:
    """Retain partial output without treating it as authoritative model history.

    Repeated terminal notifications update one request. Attachments remain drafts
    so an explicit retry can still claim them through the normal completed save.
    """
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid request identifier is required.") from None
    attachment_ids = attachment_ids or []
    if len(attachment_ids) > 4 or len(set(attachment_ids)) != len(attachment_ids):
        raise ValueError("A message can contain up to four distinct images.")
    if not user_content.strip() and not attachment_ids:
        raise ValueError("An interrupted request must include a message or attachment.")
    now = datetime.now(timezone.utc).isoformat()
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone():
            raise LookupError("Conversation not found.")
        previous = conn.execute("SELECT * FROM interrupted_turns WHERE request_id=?", (request_id,)).fetchone()
        if previous and (previous["conversation_id"] != conversation_id or previous["user_content"] != user_content):
            raise ValueError("This request identifier belongs to a different turn.")
        draft_ids = set()
        for attachment_id in attachment_ids:
            attachment = conn.execute("SELECT draft_id FROM chat_attachments WHERE id=? AND conversation_id=? AND message_id IS NULL",
                                      (attachment_id, conversation_id)).fetchone()
            if not attachment:
                raise ValueError("An attachment is unavailable or belongs to another message.")
            draft_ids.add(attachment["draft_id"])
        if len(draft_ids) > 1:
            raise ValueError("Attachments in one request must belong to the same draft.")
        if previous:
            # A delayed, shorter snapshot cannot overwrite text already saved.
            if len(assistant_content) < len(previous["assistant_content"]):
                assistant_content = previous["assistant_content"]
            if len(assistant_thinking or "") < len(previous["assistant_thinking"] or ""):
                assistant_thinking = previous["assistant_thinking"]
            cancelled = cancelled or bool(previous["cancelled"])
            if not attachment_ids:
                attachment_ids = json.loads(previous["attachment_ids"])
            conn.execute("UPDATE interrupted_turns SET assistant_content=?,assistant_thinking=?,error_message=?,cancelled=?,attachment_ids=?,updated_at=? WHERE request_id=?",
                         (assistant_content, assistant_thinking, error or previous["error_message"], int(cancelled),
                          json.dumps(attachment_ids), now, request_id))
        else:
            conn.execute("INSERT INTO interrupted_turns VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (request_id, conversation_id, user_content, assistant_content, assistant_thinking,
                          error, int(cancelled), json.dumps(attachment_ids), now, now))
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))


def save_turn(
    conversation_id: str,
    user_content: str,
    assistant_content: str,
    assistant_thinking: str | None = None,
    sources: list[dict] | None = None,
    attachment_ids: list[str] | None = None,
) -> None:
    """
    Atomically save a user + assistant message pair and bump updated_at.
    Skips if either content is empty.
    """
    if (not user_content.strip() and not attachment_ids) or not assistant_content.strip():
        return

    now = datetime.now(timezone.utc).isoformat()
    # Roll back a partial turn and release the connection even when the caller
    # retains the exception traceback. SQLite's context manager does not close it.
    with closing(_get_conn()) as conn, conn:
        user_id = str(uuid.uuid4())
        conn.executemany(
            """
            INSERT INTO conversation_messages (id, conversation_id, role, content, thinking, created_at, sources)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (user_id, conversation_id, "user",      user_content,      None,               now, "[]"),
                (str(uuid.uuid4()), conversation_id, "assistant", assistant_content, assistant_thinking, now, json.dumps(sources or [])),
            ],
        )
        if attachment_ids:
            if len(attachment_ids) > 4 or len(set(attachment_ids)) != len(attachment_ids):
                raise ValueError("A message can contain up to four distinct images.")
            for attachment_id in attachment_ids:
                updated = conn.execute(
                    "UPDATE chat_attachments SET message_id = ? "
                    "WHERE id = ? AND conversation_id = ? AND message_id IS NULL",
                    (user_id, attachment_id, conversation_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("An attachment is unavailable or belongs to another message.")
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all its messages. Returns True if it existed."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def rename_conversation(conversation_id: str, title: str) -> bool:
    """Rename a conversation. Returns True if it existed."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cursor = conn.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conversation_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated
