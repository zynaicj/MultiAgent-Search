"""Refresh long-term memory at the start of EVERY conversation turn.

DeepAgents 0.4.3's built-in MemoryMiddleware loads memory_contents only if
it is not already in the checkpointed state. That means updates performed
after a thread's first turn can otherwise be stale until a new thread begins.

Keep the official memory=[...] parameter; this extra middleware only refreshes
memory_contents before the agent models run, and does not inject twice.
"""
from langchain.agents.middleware.types import AgentMiddleware
from deepagents.middleware.memory import MemoryState

from agent.memory.native import MEMORY_SOURCE_PATH, read_native_memory


class RefreshNativeMemoryMiddleware(AgentMiddleware):
    state_schema = MemoryState

    async def abefore_agent(self, state, runtime, config):
        if runtime.store is None:
            raise RuntimeError("Native memory requires a persistent LangGraph Store")
        content = await read_native_memory(runtime.store)
        # Replacement with an empty dict also clears stale deleted memories.
        return {"memory_contents": {MEMORY_SOURCE_PATH: content} if content else {}}
