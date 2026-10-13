"""Hybrid memory policy regression tests. No live MongoDB or LLM required."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import SystemMessage
from bson import ObjectId

from agent.memory.policy import (
    MONGO_FACT_TYPES,
    deepagent_hybrid_enabled,
    filter_mongo_facts,
    keep_mongo_facts,
)
from agent.memory.hybrid import (
    _decorate_request,
    set_hybrid_memory_context,
    reset_hybrid_memory_context,
)
from agent.memory.repository import MemoryRepository
from agent.memory.schemas import MemoryRecord


class DummyRequest:
    def __init__(self, content="原始系统指令"):
        self.system_message = SystemMessage(content=content)

    def override(self, **kwargs):
        self.system_message = kwargs["system_message"]
        return self


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args):
        return self

    async def to_list(self, length):
        return self.docs[:length]


class FakeMongoCollection:
    def __init__(self):
        self.docs = []
        self.queries = []

    async def insert_one(self, doc):
        item = {"_id": ObjectId(), **doc}
        self.docs.append(item)
        return SimpleNamespace(inserted_id=item["_id"])

    def find(self, query):
        self.queries.append(query.copy())
        return Cursor([d.copy() for d in self.docs if all(d.get(k) == v for k, v in query.items())])

    async def update_one(self, query, update):
        self.queries.append(query.copy())
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(update["$set"])
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class HybridMemoryPolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_mode_is_opt_in_and_legacy_switch_supported(self):
        with patch.dict(os.environ, {"DEEPAGENT_MEMORY_MODE": "native", "DEEPAGENT_MONGO_MEMORY_ENABLED": "0"}):
            self.assertFalse(deepagent_hybrid_enabled())
        with patch.dict(os.environ, {"DEEPAGENT_MEMORY_MODE": "hybrid", "DEEPAGENT_MONGO_MEMORY_ENABLED": "0"}):
            self.assertTrue(deepagent_hybrid_enabled())
        with patch.dict(os.environ, {"DEEPAGENT_MEMORY_MODE": "native", "DEEPAGENT_MONGO_MEMORY_ENABLED": "1"}):
            self.assertTrue(deepagent_hybrid_enabled())

    def test_structured_facts_exclude_native_preferences(self):
        docs = [
            {"memory_type": "preference", "content": "喜欢中文"},
            {"memory_type": "project_fact", "content": "使用 FastAPI"},
            {"memory_type": "decision", "content": "使用 SQLite"},
        ]
        self.assertEqual(
            {"project_fact", "decision"},
            {d["memory_type"] for d in filter_mongo_facts(docs)},
        )
        records = [MemoryRecord(memory_type=d["memory_type"], content=d["content"]) for d in docs]
        self.assertEqual(
            {"project_fact", "decision"},
            {r.memory_type for r in keep_mongo_facts(records)},
        )
        self.assertEqual(MONGO_FACT_TYPES, frozenset({"user_profile", "project_fact", "decision"}))

    async def test_mongo_repository_filters_all_reads_and_updates_by_owner(self):
        """Legacy MongoDB documents without owner_id are never returned."""
        fake = FakeMongoCollection()
        repo = MemoryRepository.__new__(MemoryRepository)
        repo.collection = fake
        alice_id = await repo.save_memory(MemoryRecord(memory_type="project_fact", content="Alice 项目"), user_id="alice")
        bob_id = await repo.save_memory(MemoryRecord(memory_type="project_fact", content="Bob 项目"), user_id="bob")
        fake.docs.append({"_id": ObjectId(), "memory_type": "project_fact", "content": "旧数据，不应自动归属给任何人"})
        alice = await repo.get_all_memories(user_id="alice")
        bob = await repo.get_memories_by_type("project_fact", user_id="bob")
        self.assertEqual(["Alice 项目"], [d["content"] for d in alice])
        self.assertEqual(["Bob 项目"], [d["content"] for d in bob])
        self.assertFalse(await repo.update_memory(bob_id, "不允许串改", user_id="alice"))
        self.assertTrue(await repo.update_memory(alice_id, "Alice 新项目", user_id="alice"))
        self.assertEqual("Alice 新项目", (await repo.get_all_memories(user_id="alice"))[0]["content"])
        self.assertTrue(all("owner_id" in q for q in fake.queries))

    async def test_hybrid_context_is_ephemeral_and_task_isolated(self):
        """No Mongo content leaks into other asyncio tasks or the original user message."""
        async def answer(text):
            token = set_hybrid_memory_context(text)
            try:
                await asyncio.sleep(0)
                req = _decorate_request(DummyRequest())
                return req.system_message.content
            finally:
                reset_hybrid_memory_context(token)

        a, b = await asyncio.gather(answer("Alice 结构化事实"), answer("Bob 结构化事实"))
        self.assertIn("Alice 结构化事实", a)
        self.assertNotIn("Bob 结构化事实", a)
        self.assertIn("Bob 结构化事实", b)
        self.assertNotIn("Alice 结构化事实", b)
        self.assertEqual(_decorate_request(DummyRequest()).system_message.content, "原始系统指令")


if __name__ == "__main__":
    unittest.main()
