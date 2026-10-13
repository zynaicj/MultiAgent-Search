"""Source-of-truth rules for DeepAgents native + MongoDB long-term memory.

The DeepAgents built-in AGENTS.md is authoritative for preferences.
MongoDB is used for *structured facts*, not a duplicate preference file.
"""
import os

MONGO_FACT_TYPES = frozenset({"user_profile", "project_fact", "decision"})


def deepagent_hybrid_enabled() -> bool:
    """Opt in to MongoDB only if explicitly enabled; native memory is the default.

    DEEPAGENT_MEMORY_MODE=hybrid is preferred. The previous
    DEEPAGENT_MONGO_MEMORY_ENABLED=1 switch remains supported.
    """
    mode = os.getenv("DEEPAGENT_MEMORY_MODE", "native").strip().lower()
    if mode not in {"native", "hybrid"}:
        raise ValueError("DEEPAGENT_MEMORY_MODE must be native or hybrid")
    return mode == "hybrid" or os.getenv("DEEPAGENT_MONGO_MEMORY_ENABLED") == "1"


def keep_mongo_facts(memories):
    """Exclude preference candidates so AGENTS.md is the only preference source."""
    return [m for m in memories if m.memory_type in MONGO_FACT_TYPES]


def filter_mongo_facts(memories):
    """Exclude older MongoDB preference records when hybrid recall is enabled."""
    return [m for m in memories if m.get("memory_type") in MONGO_FACT_TYPES]
