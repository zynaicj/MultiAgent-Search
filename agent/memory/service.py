from agent.memory.extractor import extract_memories
from agent.memory.repository import memory_repository
from agent.memory.schemas import MemoryRecord


async def extract_and_save_memories(
    user_input: str,
) -> list[MemoryRecord]:
    """
    从用户输入中提取长期记忆，并保存到 MongoDB。

    执行流程：
    1. 调用 Memory Extractor 提取长期记忆
    2. 将每条 MemoryRecord 写入 MongoDB
    3. 返回本轮成功保存的长期记忆

    如果用户输入中没有值得保存的信息，
    则直接返回空列表。
    """

    memories = await extract_memories(user_input)

    if not memories:
        return []

    for memory in memories:
        await memory_repository.save_memory(memory)

    return memories