"""Integration test for hybrid Mongo context in the real DeepAgents middleware stack."""
import tempfile
import unittest
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent.memory.hybrid import (
    HybridMongoContextMiddleware,
    reset_hybrid_memory_context,
    set_hybrid_memory_context,
)
from agent.memory.native import (
    MEMORY_SOURCE_PATH,
    backend_factory_for,
    seed_native_memory,
)
from agent.memory.refresh import RefreshNativeMemoryMiddleware

FACT = "PRIVATE_MONGO_FACT_FOR_INJECTION_TEST"


class ObserveHybridModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "observe-hybrid-middleware"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        in_system = any(
            FACT in str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
        )
        in_user = any(
            FACT in str(message.content)
            for message in messages
            if message.type == "human"
        )
        content = ("SYSTEM_SEEN" if in_system else "SYSTEM_MISSING")
        if in_user:
            content = "UNEXPECTED_USER_INJECTION"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


class HybridAgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_mongo_fact_is_system_only_and_not_replayed_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            async with AsyncSqliteStore.from_conn_string(str(Path(temp) / "store.sqlite")) as store:
                await store.setup()
                await seed_native_memory(store)
                agent = create_deep_agent(
                    model=ObserveHybridModel(),
                    store=store,
                    checkpointer=InMemorySaver(),
                    backend=backend_factory_for(),
                    memory=[MEMORY_SOURCE_PATH],
                    middleware=[RefreshNativeMemoryMiddleware(), HybridMongoContextMiddleware()],
                )
                config = {"configurable": {"thread_id": "one-conversation"}}

                async def query_with_context(mongo_context):
                    token = set_hybrid_memory_context(mongo_context)
                    try:
                        return await agent.ainvoke(
                            {"messages": [{"role": "user", "content": "请回答当前问题"}]},
                            config=config,
                        )
                    finally:
                        reset_hybrid_memory_context(token)

                first = await query_with_context(FACT)
                self.assertEqual(first["messages"][-1].content, "SYSTEM_SEEN")
                self.assertFalse(any(FACT in str(m.content) for m in first["messages"]))

                # Same Checkpoint thread, but no recalled Mongo facts this turn.
                second = await query_with_context("")
                self.assertEqual(second["messages"][-1].content, "SYSTEM_MISSING")
                self.assertFalse(any(FACT in str(m.content) for m in second["messages"]))


if __name__ == "__main__":
    unittest.main()
