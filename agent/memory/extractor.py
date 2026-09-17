from agent.llm import memory_extractor_model
from agent.memory.schemas import MemoryExtractionResult, MemoryRecord


MEMORY_EXTRACTION_PROMPT = """
你是一个长期记忆提取器。

你的任务是分析用户本轮输入，只提取未来继续帮助用户时具有长期价值的信息。

允许保存的长期记忆类型只有以下四种：

1. user_profile
用户相对稳定的背景信息。
例如：
- 用户是研究生
- 用户正在寻找 Agent 开发实习

2. preference
用户明确表达的长期偏好、习惯或者希望后续持续遵守的方式。
例如：
- 用户希望修改代码时直接提供完整文件
- 用户进行临时 Python 测试时偏好统一使用 temp_test.py

3. project_fact
当前长期项目中比较稳定、未来仍可能使用的事实。
例如：
- MultiAgent-Search 使用 FastAPI 提供后端服务
- 项目的长期记忆使用 MongoDB 存储

4. decision
用户已经明确做出的、未来开发需要继续遵守的决定。
例如：
- 用户决定使用 MongoDB 实现长期记忆
- 用户决定优先开发多 Agent 协作能力

以下内容不要保存：
- 普通问候
- 临时问题
- 一次性的报错信息
- 当前任务执行过程中的临时状态
- 很快会失效的信息
- 助手自己的建议
- 用户没有明确确认的推测
- 没有长期价值的琐碎实现细节

要求：
1. 只根据用户明确说出的内容提取，不要猜测。
2. 将记忆整理成能够脱离当前对话独立理解的一句话。
3. 不要保存重复、无意义或者过于琐碎的信息。
4. 一条用户输入可以产生零条、一条或者多条记忆。
5. 如果没有值得长期保存的信息，memories 返回空列表。
"""


structured_memory_model = memory_extractor_model.with_structured_output(
    MemoryExtractionResult,
    method="function_calling",
)


async def extract_memories(user_input: str) -> list[MemoryRecord]:
    """
    从一轮用户输入中提取值得长期保存的记忆。

    当前只负责：
    用户输入 -> LLM -> 结构化 MemoryRecord

    暂时不负责写入 MongoDB。
    """

    user_input = user_input.strip()

    if not user_input:
        return []

    result = await structured_memory_model.ainvoke(
        [
            (
                "system",
                MEMORY_EXTRACTION_PROMPT,
            ),
            (
                "human",
                f"用户本轮输入：\n{user_input}",
            ),
        ]
    )

    return [
        MemoryRecord(
            memory_type=memory.memory_type,
            content=memory.content,
        )
        for memory in result.memories
    ]