"""Local, API-free tests for durable chat state."""
import tempfile
import unittest
from pathlib import Path

from api.chat import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = store.DB_PATH
        store.DB_PATH = Path(self.tmp.name) / "test.db"
        store.init()

    def tearDown(self):
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_turns_are_atomic_and_idempotent(self):
        cid = store.create_conversation(mode="deep_agent")["id"]
        status, run = store.begin_run(cid, "你好", "client-a")
        self.assertEqual(status, "started")
        self.assertEqual(store.begin_run(cid, "你好", "client-a")[0], "duplicate")
        self.assertEqual(store.begin_run(cid, "第二条", "client-b")[0], "busy")
        store.finish_run(run["id"], "completed", answer="你好！")
        self.assertEqual(store.get_conversation(cid)["status"], "idle")
        self.assertEqual([m["role"] for m in store.list_messages(cid)], ["user", "assistant"])
        self.assertEqual(store.begin_run(cid, "第二条", "client-b")[0], "started")

    def test_waiting_approval_and_resume(self):
        cid = store.create_conversation()["id"]
        _, run = store.begin_run(cid, "修改测试数据", "client-a")
        payload = {"thread_id": cid, "tool_name": "execute_sql_query", "operation": "UPDATE"}
        store.finish_run(run["id"], "waiting_approval", approval=payload)
        self.assertEqual(store.get_conversation(cid)["status"], "waiting_approval")
        self.assertEqual(store.delete_conversation(cid), "busy")
        self.assertEqual(store.begin_resume(cid, run["id"]), "started")
        self.assertEqual(store.begin_resume(cid, run["id"]), "not_waiting")
        store.finish_run(run["id"], "completed", answer="已审批并执行")
        self.assertEqual(store.delete_conversation(cid), "deleted")

    def test_event_pagination_and_crash_recovery(self):
        cid = store.create_conversation()["id"]
        _, run = store.begin_run(cid, "任务", "client-a")
        event_id = store.append_event(cid, run["id"], "tool_start", "运行", {"tool": "demo"})
        events = store.list_events(cid)
        self.assertEqual(events[0]["id"], event_id)
        self.assertEqual(events[0]["data"]["tool"], "demo")
        self.assertEqual(store.list_events(cid, after=event_id), [])
        store.restore_interrupted_runs()
        self.assertEqual(store.get_conversation(cid)["status"], "idle")
        self.assertEqual(store.get_run(run["id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
