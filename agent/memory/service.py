from agent.memory.extractor import extract_memories
from agent.memory.manager import decide_memory_action
from agent.memory.repository import memory_repository
from agent.memory.schemas import (
    MemoryProcessResult,
    MemoryRecord,
)


async def extract_and_manage_memories(
    user_input: str,
) -> list[MemoryProcessResult]:
    """
    从用户输入中提取长期记忆，
    并根据已有记忆执行新增、跳过或更新。

    流程：
    1. Extractor 提取候选长期记忆
    2. Repository 查询同类型已有记忆
    3. Manager 判断 add / skip / update
    4. Service 执行对应数据库操作
    """

    memories = await extract_memories(
        user_input
    )

    if not memories:
        return []

    results = []

    for memory in memories:
        existing_memories = (
            await memory_repository.get_memories_by_type(
                memory.memory_type
            )
        )

        decision = await decide_memory_action(
            memory,
            existing_memories,
        )

        if decision.action == "add":
            final_memory = MemoryRecord(
                memory_type=memory.memory_type,
                content=decision.final_content,
            )

            memory_id = await memory_repository.save_memory(
                final_memory
            )

            results.append(
                MemoryProcessResult(
                    action="add",
                    memory_id=memory_id,
                    memory_type=memory.memory_type,
                    content=decision.final_content,
                    reason=decision.reason,
                )
            )

            continue

        if decision.action == "skip":
            results.append(
                MemoryProcessResult(
                    action="skip",
                    memory_id=decision.target_memory_id,
                    memory_type=memory.memory_type,
                    content=decision.final_content,
                    reason=decision.reason,
                )
            )

            continue

        if not decision.target_memory_id:
            raise ValueError(
                "Memory Manager 返回 update，"
                "但没有提供 target_memory_id"
            )

        existing_ids = {
            item["_id"]
            for item in existing_memories
        }

        if decision.target_memory_id not in existing_ids:
            raise ValueError(
                "Memory Manager 返回了不存在的 "
                "target_memory_id"
            )

        updated = await memory_repository.update_memory(
            decision.target_memory_id,
            decision.final_content,
        )

        if not updated:
            raise ValueError(
                "MongoDB 长期记忆更新失败"
            )

        results.append(
            MemoryProcessResult(
                action="update",
                memory_id=decision.target_memory_id,
                memory_type=memory.memory_type,
                content=decision.final_content,
                reason=decision.reason,
            )
        )

    return results