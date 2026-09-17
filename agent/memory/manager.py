from agent.llm import memory_extractor_model
from agent.memory.schemas import (
    MemoryManagementDecision,
    MemoryRecord,
)


MEMORY_MANAGEMENT_PROMPT = """
你是一个长期记忆管理器。

现在会给你：
1. 一条刚刚提取出来的新长期记忆
2. 数据库中已有的、与它类型相同的长期记忆

你必须判断应该执行以下哪一种操作：

1. add
新记忆表达的是一个独立的新信息，已有记忆中没有表达这个事实。

2. skip
新记忆和某条已有记忆表达的是相同或基本相同的信息，
没有提供值得更新的新内容。

3. update
新记忆修改、纠正、替代了某条已有记忆。
例如：
旧记忆：用户偏好使用 temp_test.py 进行临时测试
新记忆：用户以后改用 debug_test.py，不再使用 temp_test.py
这种情况应该 update，而不是同时保留两条互相冲突的记忆。

要求：
- 不要因为两条记忆属于同一个类型就认为它们重复。
- preference 类型可以同时存在多个不同偏好。
- project_fact 类型可以同时存在多个不同项目事实。
- decision 类型可以同时存在多个不同决定。
- user_profile 类型可以同时存在多个不同用户信息。
- 只有语义相同才 skip。
- 只有新信息明显修改或替代某条旧信息才 update。
- update 时 target_memory_id 必须填写被替换的旧记忆 ID。
- skip 时 target_memory_id 应填写对应的重复记忆 ID。
- add 时 target_memory_id 必须为空。
- final_content 必须是一句能够脱离当前对话独立理解的长期记忆。
"""


structured_memory_manager = memory_extractor_model.with_structured_output(
    MemoryManagementDecision,
    method="function_calling",
)


def _normalize_content(content: str) -> str:
    """
    用于快速判断完全相同的记忆。

    这里只去掉空白并统一成小写，
    语义级别的重复仍然交给 LLM 判断。
    """

    return "".join(content.lower().split())


async def decide_memory_action(
    new_memory: MemoryRecord,
    existing_memories: list[dict],
) -> MemoryManagementDecision:
    """
    判断新记忆应该 add、skip 还是 update。
    """

    if not existing_memories:
        return MemoryManagementDecision(
            action="add",
            final_content=new_memory.content,
            reason="当前没有同类型长期记忆，因此直接新增",
        )

    normalized_new_content = _normalize_content(
        new_memory.content
    )

    for existing_memory in existing_memories:
        existing_content = existing_memory.get(
            "content",
            "",
        )

        if _normalize_content(existing_content) == normalized_new_content:
            return MemoryManagementDecision(
                action="skip",
                target_memory_id=existing_memory["_id"],
                final_content=existing_content,
                reason="新记忆与已有记忆内容完全相同",
            )

    existing_memory_text = "\n".join(
        (
            f"- id: {memory['_id']}\n"
            f"  content: {memory.get('content', '')}"
        )
        for memory in existing_memories
    )

    result = await structured_memory_manager.ainvoke(
        [
            (
                "system",
                MEMORY_MANAGEMENT_PROMPT,
            ),
            (
                "human",
                (
                    "新长期记忆：\n"
                    f"type: {new_memory.memory_type}\n"
                    f"content: {new_memory.content}\n\n"
                    "已有同类型长期记忆：\n"
                    f"{existing_memory_text}"
                ),
            ),
        ]
    )

    return result