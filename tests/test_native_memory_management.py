"""Native memory: dedup/edit/delete and same-thread refresh integration.

Uses a local fake LLM: no Qwen API, MongoDB, or web calls are needed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent.memory.manage import delete_preference, list_preferences, set_preference
from agent.memory.native import MEMORY_SOURCE_PATH, backend_factory_for, read_native_memory, seed_native_memory
from agent.memory.refresh import RefreshNativeMemoryMiddleware
from agent.memory import native as native_module
from agent.memory.tools import (
    save_long_term_preference,
    list_long_term_preferences,
    forget_long_term_preference,
)

MARKER = "偏好标记ABC_8721"


class MemoryProbe(BaseChatModel):
    @property
    def _llm_type(self):
        return "native-memory-check"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        saw = any(
            MARKER in str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
        )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="FOUND" if saw else "MISSING"))]
        )


class NativeMemoryManagementTest(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicate_replace_delete_and_namespace_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            async with AsyncSqliteStore.from_conn_string(str(Path(tmp) / "store.db")) as store:
                await store.setup()
                await seed_native_memory(store, "alice")
                await seed_native_memory(store, "bob")
                self.assertEqual("added", await set_preference(store, "reply_style", "简洁", "alice"))
                self.assertEqual("unchanged", await set_preference(store, "reply_style", "简洁", "alice"))
                self.assertEqual("updated", await set_preference(store, "reply_style", "详细注释", "alice"))
                self.assertEqual({"reply_style": "详细注释"}, await list_preferences(store, "alice"))
                self.assertEqual({}, await list_preferences(store, "bob"))
                self.assertEqual((await read_native_memory(store, "alice")).count("**reply_style**"), 1)
                self.assertTrue(await delete_preference(store, "reply_style", "alice"))
                self.assertFalse(await delete_preference(store, "reply_style", "alice"))
                self.assertEqual({}, await list_preferences(store, "alice"))
                with self.assertRaises(ValueError):
                    await set_preference(store, "api_key", "should-not-store", "alice")
                with self.assertRaises(ValueError):
                    await set_preference(store, "abc", "line1" + chr(10) + "line2", "alice")

    async def test_registered_preference_tools_verify_the_persisted_data(self):
        """Tool messages are grounded in read-back of the actual SQLite record."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tool_memory.sqlite"
            with patch.object(native_module, "NATIVE_STORE_PATH", db):
                try:
                    result = await save_long_term_preference.ainvoke({
                        "key": "reply_style", "value": "详细中文注释"
                    })
                    self.assertIn("持久化校验成功", result)
                    self.assertIn("added", result)
                    saved = await list_long_term_preferences.ainvoke({})
                    self.assertIn("reply_style: 详细中文注释", saved)
                    result = await save_long_term_preference.ainvoke({
                        "key": "reply_style", "value": "详细中文注释"
                    })
                    self.assertIn("unchanged", result)
                    deleted = await forget_long_term_preference.ainvoke({
                        "key": "reply_style"
                    })
                    self.assertIn("已从持久化存储删除", deleted)
                    saved = await list_long_term_preferences.ainvoke({})
                    self.assertIn("没有通过偏好管理工具保存", saved)
                finally:
                    await native_module.close_native_store()

    async def test_actual_deepagent_refreshes_same_thread_after_memory_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "store.db")
            async with AsyncSqliteStore.from_conn_string(db) as store:
                await store.setup()
                await seed_native_memory(store)
                agent = create_deep_agent(
                    model=MemoryProbe(),
                    checkpointer=InMemorySaver(),
                    backend=backend_factory_for(),
                    memory=[MEMORY_SOURCE_PATH],
                    middleware=[RefreshNativeMemoryMiddleware()],
                    store=store,
                )
                config = {"configurable": {"thread_id": "same-thread"}}

                async def ask():
                    result = await agent.ainvoke(
                        {"messages": [{"role": "user", "content": "记忆测试"}]}, config=config
                    )
                    return result["messages"][-1].content

                self.assertEqual("MISSING", await ask())
                self.assertEqual("added", await set_preference(store, "reply_style", MARKER))
                self.assertEqual("FOUND", await ask())
                self.assertEqual("updated", await set_preference(store, "reply_style", "更换偏好"))
                self.assertEqual("MISSING", await ask())
                self.assertTrue(await delete_preference(store, "reply_style"))
                self.assertEqual("MISSING", await ask())

            # Separate connection simulates service restart; updated preference is deleted.
            async with AsyncSqliteStore.from_conn_string(db) as store:
                await store.setup()
                self.assertEqual({}, await list_preferences(store))


if __name__ == "__main__":
    unittest.main()
