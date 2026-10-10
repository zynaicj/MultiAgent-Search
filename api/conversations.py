"""SQLite 会话和可展示消息，与 DeepAgents checkpoint 分开存储。"""
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "conversations.db"
DB.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _lock, _connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle')""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)")


def all_conversations():
    with _lock, _connect() as c:
        return [dict(x) for x in c.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]


def get_conversation(cid):
    with _lock, _connect() as c:
        row = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None


def create_conversation(cid=None, title="新聊天"):
    cid = cid or str(uuid.uuid4())
    now = _now()
    with _lock, _connect() as c:
        c.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)", (cid, title[:80], now, now))
    return get_conversation(cid)


def rename_conversation(cid, title):
    with _lock, _connect() as c:
        c.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (title[:80], _now(), cid))


def set_status(cid, status):
    with _lock, _connect() as c:
        c.execute("UPDATE conversations SET status=?,updated_at=? WHERE id=?", (status, _now(), cid))


def add_message(cid, role, content):
    now = _now()
    with _lock, _connect() as c:
        c.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid.uuid4()), cid, role, str(content), now))
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))


def get_messages(cid):
    with _lock, _connect() as c:
        return [dict(x) for x in c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (cid,))]


def delete_conversation(cid):
    with _lock, _connect() as c:
        c.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))


init_db()
