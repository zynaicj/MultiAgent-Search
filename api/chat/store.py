"""SQLite chat history and execution journal, independent of LangGraph checkpoints."""
import json
from contextlib import contextmanager
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "conversations.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()
ACTIVE = {"running", "waiting_approval"}
VALID_MODES = {"deep_agent", "collaboration"}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _db():
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA foreign_keys=ON")
        with con:
            yield con
    finally:
        con.close()


def init():
    with _LOCK, _db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle',
            current_run_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            client_message_id TEXT NOT NULL,
            query TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
            approval_json TEXT, created_at TEXT NOT NULL, finished_at TEXT,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            UNIQUE(conversation_id, client_message_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages ON messages(conversation_id, created_at);
        CREATE TABLE IF NOT EXISTS run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL, run_id TEXT,
            event TEXT NOT NULL, message TEXT NOT NULL, data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_events ON run_events(conversation_id, id);
        """)


def create_conversation(title="新聊天", mode="deep_agent"):
    if mode not in VALID_MODES:
        raise ValueError("不支持的会话模式")
    cid = str(uuid.uuid4())
    timestamp = now()
    with _LOCK, _db() as con:
        con.execute(
            "INSERT INTO conversations(id,title,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (cid, (title.strip() or "新聊天")[:80], mode, "idle", timestamp, timestamp),
        )
    return get_conversation(cid)


def get_conversation(cid):
    with _LOCK, _db() as con:
        row = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None


def list_conversations():
    with _LOCK, _db() as con:
        return [dict(row) for row in con.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]


def rename_conversation(cid, title):
    with _LOCK, _db() as con:
        cur = con.execute(
            "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
            ((title.strip() or "新聊天")[:80], now(), cid),
        )
        return cur.rowcount > 0


def delete_conversation(cid):
    with _LOCK, _db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT status FROM conversations WHERE id=?", (cid,)).fetchone()
        if not row:
            con.rollback()
            return "missing"
        if row["status"] in ACTIVE:
            con.rollback()
            return "busy"
        con.execute("DELETE FROM conversations WHERE id=?", (cid,))
        con.commit()
        return "deleted"


def list_messages(cid):
    with _LOCK, _db() as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (cid,)
        )]


def list_events(cid, after=0, limit=300):
    with _LOCK, _db() as con:
        rows = con.execute(
            "SELECT * FROM run_events WHERE conversation_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (cid, max(0, after), min(max(1, limit), 1000)),
        ).fetchall()
    return [
        {**dict(row), "data": json.loads(row["data_json"])}
        for row in rows
    ]


def append_event(cid, run_id, event, message, data):
    with _LOCK, _db() as con:
        cur = con.execute(
            "INSERT INTO run_events(conversation_id,run_id,event,message,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (cid, run_id, event, str(message), json.dumps(data, ensure_ascii=False, default=str), now()),
        )
        return cur.lastrowid


def begin_run(cid, query, client_message_id):
    """Atomic per-conversation busy check and idempotency guard."""
    with _LOCK, _db() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT * FROM runs WHERE conversation_id=? AND client_message_id=?",
            (cid, client_message_id),
        ).fetchone()
        if previous:
            con.commit()
            return "duplicate", dict(previous)
        conversation = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not conversation:
            con.rollback()
            return "missing", None
        if conversation["status"] in ACTIVE:
            con.rollback()
            return "busy", None
        rid = str(uuid.uuid4())
        timestamp = now()
        con.execute(
            "INSERT INTO runs(id,conversation_id,client_message_id,query,status,created_at) VALUES(?,?,?,?,?,?)",
            (rid, cid, client_message_id, query, "running", timestamp),
        )
        con.execute(
            "INSERT INTO messages(id,conversation_id,run_id,role,content,created_at) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), cid, rid, "user", query, timestamp),
        )
        con.execute(
            "UPDATE conversations SET status='running',current_run_id=?,updated_at=? WHERE id=?",
            (rid, timestamp, cid),
        )
        con.commit()
        return "started", {"id": rid, "conversation_id": cid, "status": "running"}


def get_run(rid):
    with _LOCK, _db() as con:
        row = con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None


def latest_run(cid):
    with _LOCK, _db() as con:
        row = con.execute(
            "SELECT * FROM runs WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (cid,)
        ).fetchone()
        return dict(row) if row else None


def finish_run(rid, status, answer=None, error=None, approval=None):
    if status not in {"completed", "failed", "waiting_approval"}:
        raise ValueError("不合法的结束状态")
    with _LOCK, _db() as con:
        con.execute("BEGIN IMMEDIATE")
        run = con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if not run:
            con.rollback()
            return
        cid = run["conversation_id"]
        con.execute(
            "UPDATE runs SET status=?,error=?,approval_json=?,finished_at=? WHERE id=?",
            (
                status, error, json.dumps(approval, ensure_ascii=False, default=str) if approval else None,
                now() if status != "waiting_approval" else None, rid,
            ),
        )
        if status == "completed" and answer:
            exists = con.execute(
                "SELECT 1 FROM messages WHERE run_id=? AND role='assistant'", (rid,)
            ).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO messages(id,conversation_id,run_id,role,content,created_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), cid, rid, "assistant", str(answer), now()),
                )
        con.execute(
            "UPDATE conversations SET status=?,current_run_id=?,updated_at=? WHERE id=?",
            ("waiting_approval" if status == "waiting_approval" else "idle",
             rid if status == "waiting_approval" else None, now(), cid),
        )
        con.commit()


def begin_resume(cid, rid):
    with _LOCK, _db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM runs WHERE id=? AND conversation_id=?", (rid, cid)
        ).fetchone()
        if not row:
            con.rollback()
            return "missing"
        if row["status"] != "waiting_approval":
            con.rollback()
            return "not_waiting"
        con.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
        con.execute("UPDATE conversations SET status='running',updated_at=? WHERE id=?", (now(), cid))
        con.commit()
        return "started"


def restore_interrupted_runs():
    """A process restart aborts in-flight coroutine tasks, but keeps pending interrupts."""
    with _LOCK, _db() as con:
        con.execute("UPDATE runs SET status='failed',error='服务已重启，请重新提交本轮问题',finished_at=? WHERE status='running'", (now(),))
        con.execute(
            "UPDATE conversations SET status='idle',current_run_id=NULL WHERE status='running'"
        )
        rows = con.execute(
            "SELECT * FROM runs WHERE status='waiting_approval'"
        ).fetchall()
        return [dict(r) for r in rows]


init()
