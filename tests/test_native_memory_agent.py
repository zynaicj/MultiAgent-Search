"""Check create_deep_agent actually injects AGENTS.md through MemoryMiddleware."""
import tempfile
import unittest
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.utils import create_file_data
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent.memory.native import (
    MEMORY_SOURCE_PATH,
    STORE_KEY,
    backend_factory_for,
    memory_namespace,
    seed_native_memory,
)

TOKEN = "ALICE_NATIVE_MEMORY_SENTINEL"


class ObserveSystemPrompt(BaseChatModel):
    @property
    def _llm_type(self):
        return "observe-system-prompt"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        visible = any(
            TOKEN in str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
        )
        reply = AIMessage(content="MEMORY_VISIBLE" if visible else "MEMORY_MISSING")
        return ChatResult(generations=[ChatGeneration(message=reply)])


class AgentMemoryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepagent_reads_same_user_memory_in_new_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            async with AsyncSqliteStore.from_conn_string(str(Path(tmp) / "memory.sqlite")) as store:
                await store.setup()
                await seed_native_memory(store, "alice")
                await seed_native_memory(store, "bob")
                await store.aput(
                    memory_namespace("alice"),
                    STORE_KEY,
                    create_file_data("# 用户偏好\\n" + TOKEN),
                )
                for user_id, expected in [
                    ("alice", "MEMORY_VISIBLE"),
                    ("bob", "MEMORY_MISSING"),
                ]:
                    agent = create_deep_agent(
                        model=ObserveSystemPrompt(),
                        memory=[MEMORY_SOURCE_PATH],
                        backend=backend_factory_for(user_id),
                        store=store,
                        checkpointer=InMemorySaver(),
                    )
                    for tid in ("thread-one", "thread-two"):
                        result = await agent.ainvoke(
                            {"messages": [{"role": "user", "content": "告诉我有没有记忆"}]},
                            config={"configurable": {"thread_id": user_id + "-" + tid}},
                        )
                        self.assertEqual(result["messages"][-1].content, expected)


if __name__ == "__main__":
    unittest.main()
