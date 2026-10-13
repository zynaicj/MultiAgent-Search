"""DeepAgents native long-term memory storage tests (no LLM or MongoDB needed)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from deepagents.backends.utils import file_data_to_string
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent.memory.native import (
    INITIAL_MEMORY,
    MEMORY_SOURCE_PATH,
    STORE_KEY,
    backend_factory_for,
    memory_namespace,
    read_native_memory,
    seed_native_memory,
)


class NativeMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_thread_storage_user_isolation_and_restart(self):
        """StoreBackend persists bytes across entirely new graph threads and restarts."""
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "native_memory.db")
            async with AsyncSqliteStore.from_conn_string(db_path) as store:
                await store.setup()
                await seed_native_memory(store, "alice")
                await seed_native_memory(store, "bob")
                self.assertIn("用户长期记忆", await read_native_memory(store, "alice"))
                runtime = SimpleNamespace(store=store, state={})
                backend = backend_factory_for("alice")(runtime)

                # This is the same path the LLM's edit_file tool can use.
                result = await backend.aedit(
                    MEMORY_SOURCE_PATH,
                    "当前没有记录任何稳定的用户偏好或项目事实。",
                    "用户偏好：以后讲 Python 代码要写详细中文注释。",
                )
                self.assertIsNone(result.error)
                file_response = await backend.adownload_files([MEMORY_SOURCE_PATH])
                self.assertEqual(len(file_response), 1)
                self.assertIn("详细中文注释", file_response[0].content.decode("utf-8"))
                self.assertNotIn("详细中文注释", await read_native_memory(store, "bob"))
                self.assertIn("详细中文注释", await read_native_memory(store, "alice"))

            # Simulate FastAPI shutdown and restart with the same SQLite file.
            async with AsyncSqliteStore.from_conn_string(db_path) as store:
                await store.setup()
                await seed_native_memory(store, "alice")
                content = await read_native_memory(store, "alice")
                self.assertIn("详细中文注释", content)
                self.assertNotEqual(content, INITIAL_MEMORY)
                backend2 = backend_factory_for("alice")(SimpleNamespace(store=store, state={}))
                response = await backend2.adownload_files([MEMORY_SOURCE_PATH])
                self.assertIn("详细中文注释", response[0].content.decode("utf-8"))
                self.assertIsNone((await store.aget(memory_namespace("bob"), STORE_KEY)).value.get("missing"))

    async def test_no_untrusted_user_ids(self):
        with self.assertRaises(ValueError):
            memory_namespace("../private")
        with self.assertRaises(ValueError):
            memory_namespace("")


if __name__ == "__main__":
    unittest.main()
