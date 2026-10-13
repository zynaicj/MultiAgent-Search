"""DeepAgents 0.4.3 native long-term memory on a persistent LangGraph Store.

The virtual path /memories/AGENTS.md maps to the store key /AGENTS.md
under ("deepagents_memory", trusted_user_id). Never derive user ids from
unauthenticated chat text or HTTP headers.
"""
import os
import re
from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from deepagents.backends.store import StoreBackend
from deepagents.backends.utils import create_file_data, file_data_to_string
from langgraph.store.sqlite.aio import AsyncSqliteStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NATIVE_STORE_PATH = PROJECT_ROOT / "data" / "native_memory.db"
MEMORY_SOURCE_PATH = "/memories/AGENTS.md"
STORE_KEY = "/AGENTS.md"
DEFAULT_USER_ID = os.getenv("NATIVE_MEMORY_USER_ID", "local_user")
_STORE_CTX = None
_STORE = None

# This initial content makes MemoryMiddleware's first read deterministic.
INITIAL_MEMORY = """# 用户长期记忆

当前没有记录任何稳定的用户偏好或项目事实。

仅保存用户明确要求记住的非敏感、长期有效的信息。
"""


def memory_namespace(user_id: str = DEFAULT_USER_ID) -> tuple[str, str]:
    """Return a stable cross-thread namespace, with a safe logical user key."""
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", user_id):
        raise ValueError("invalid trusted memory user id")
    return ("deepagents_memory", user_id)


def backend_factory_for(user_id: str = DEFAULT_USER_ID):
    """Create the backend factory required by deepagents==0.4.3.

    Only /memories/ is stored across threads. Other virtual files stay in
    LangGraph state and are subject to its Checkpointer.
    """
    namespace = memory_namespace(user_id)

    def factory(runtime):
        return CompositeBackend(
            default=StateBackend(runtime),
            routes={
                "/memories/": StoreBackend(
                    runtime,
                    namespace=lambda _ctx: namespace,
                )
            },
        )

    return factory


async def seed_native_memory(store, user_id: str = DEFAULT_USER_ID):
    """Create AGENTS.md once; never overwrite edits from a previous run."""
    namespace = memory_namespace(user_id)
    existing = await store.aget(namespace, STORE_KEY)
    if existing is None:
        await store.aput(namespace, STORE_KEY, create_file_data(INITIAL_MEMORY))


async def read_native_memory(store, user_id: str = DEFAULT_USER_ID) -> str:
    """Read the same persisted file that MemoryMiddleware loads at run start."""
    item = await store.aget(memory_namespace(user_id), STORE_KEY)
    if item is None:
        return ""
    return file_data_to_string(item.value)


async def init_native_store():
    """Open a real SQLite-backed BaseStore for the lifetime of FastAPI."""
    global _STORE_CTX, _STORE
    if _STORE is not None:
        return _STORE
    NATIVE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ctx = AsyncSqliteStore.from_conn_string(str(NATIVE_STORE_PATH))
    store = await ctx.__aenter__()
    try:
        await store.setup()
        await seed_native_memory(store)
    except Exception:
        await ctx.__aexit__(None, None, None)
        raise
    _STORE_CTX = ctx
    _STORE = store
    return store


async def close_native_store():
    """Close the SQLite store connection, without deleting its on-disk data."""
    global _STORE_CTX, _STORE
    ctx = _STORE_CTX
    _STORE_CTX = None
    _STORE = None
    if ctx is not None:
        await ctx.__aexit__(None, None, None)
