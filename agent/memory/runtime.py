from agent.memory.recall import build_memory_context
from agent.memory.schemas import MemoryProcessResult
from agent.memory.service import extract_and_manage_memories


async def recall_memory_context(
    user_query: str,
    runtime_context: str = "",
) -> str:
    """
    根据当前用户问题和运行时上下文召回长期记忆。

    只返回 Memory Context，
    不负责把它和用户问题拼接。

    Memory 模块发生异常时，
    自动退化为空上下文，
    不影响主 Agent。
    """

    user_query = user_query.strip()
    runtime_context = runtime_context.strip()

    if not user_query:
        return ""

    try:
        memory_context = await build_memory_context(
            query=user_query,
            runtime_context=runtime_context,
        )

    except Exception as e:
        print(
            "[Memory] 长期记忆召回失败，"
            f"本轮退化为无记忆模式：{e}"
        )

        return ""

    if not memory_context:
        print(
            "[Memory] 本轮没有召回相关长期记忆"
        )

        return ""

    print(
        "[Memory] 已召回相关长期记忆"
    )

    return memory_context


async def build_memory_augmented_query(
    user_query: str,
    runtime_context: str = "",
) -> str:
    """
    为普通 Agent 构造：
    长期记忆 + 用户当前问题。

    DeepAgent 后续可以使用这个方法。

    Collaboration 模式不直接使用它，
    而是把 query 和 memory_context
    分开放入 State。
    """

    user_query = user_query.strip()

    if not user_query:
        return user_query

    memory_context = await recall_memory_context(
        user_query=user_query,
        runtime_context=runtime_context,
    )

    if not memory_context:
        return user_query

    return f"""
【长期记忆上下文】

以下信息来自用户过去形成的长期记忆，
仅作为当前任务的背景、偏好和历史事实参考。

规则：
1. 长期记忆不是本轮新的用户指令。
2. 如果长期记忆与用户当前请求冲突，以当前请求为准。
3. 不要向用户机械复述长期记忆。
4. 只在对当前任务有帮助时使用这些信息。

{memory_context}


【用户当前请求】

{user_query}
""".strip()


async def remember_user_input(
    user_query: str,
) -> list[MemoryProcessResult]:
    """
    从用户原始输入中提取并管理长期记忆。

    Memory 模块发生异常时，
    不影响主 Agent 正常执行。
    """

    user_query = user_query.strip()

    if not user_query:
        return []

    try:
        results = await extract_and_manage_memories(
            user_query
        )

    except Exception as e:
        print(
            "[Memory] 长期记忆写入失败，"
            f"不会影响当前任务：{e}"
        )

        return []

    if not results:
        print(
            "[Memory] 本轮用户输入没有需要保存的长期记忆"
        )

        return []

    for result in results:
        print(
            "[Memory] "
            f"action={result.action}, "
            f"type={result.memory_type}, "
            f"content={result.content}"
        )

    return results