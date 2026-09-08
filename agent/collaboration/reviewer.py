from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_core.output_parsers import (
    PydanticOutputParser,
)

from agent.llm import model
from agent.collaboration.schemas import (
    ReviewResult,
)
from agent.collaboration.state import (
    CollaborationState,
)

from api.monitor import monitor


REVIEWER_SYSTEM_PROMPT = """
你是多智能体协作系统中的质量审核智能体 Reviewer。

你的职责不是重新回答用户问题，
而是审核 Synthesizer 生成的答案草稿是否已经达到交付标准。

你需要结合：

1. 用户原始问题
2. Planner 生成的任务计划
3. 各 Worker 的实际执行结果
4. Synthesizer 生成的答案草稿

进行审核。

审核重点：

1. 完整性
   - 用户要求的内容是否都被覆盖
   - Planner 中的重要任务结果是否都被使用

2. 证据一致性
   - 草稿中的结论是否能够被 Worker 结果支持
   - 不允许出现明显脱离 Worker 结果的编造内容

3. 多 Agent 信息融合
   - 是否真正综合了多个 Worker 的结果
   - 是否只是简单机械拼接

4. Worker 失败情况
   - 如果某个重要 Worker 执行失败，
     需要判断是否导致答案缺失关键信息

5. 可交付性
   - 答案是否清晰、完整、逻辑合理
   - 是否已经能够直接交付给用户

审核规则：

- score 范围必须是 0 到 1
- passed=true 表示当前答案已经可以直接交付
- passed=false 表示仍然需要补充信息或重新执行部分任务
- missing_items 用于描述当前缺失的信息
- retry_tasks 只填写真正需要重新执行或补充执行的任务
- 如果 passed=true，则 retry_tasks 必须为空列表 []
- 如果 passed=false 且缺失信息需要 Worker 补充，
  应生成对应的 retry_tasks
- retry_tasks 中只能使用系统已有的三个专业智能体：
  市场情报助手、数据分析助手、内部知识助手
- retry task 的 depends_on 当前仍然必须为 []
- 不要为了制造 Retry 而故意判定失败
- 如果答案已经充分完成用户要求，应正常通过
"""


reviewer_parser = PydanticOutputParser(
    pydantic_object=ReviewResult
)


async def reviewer_node(
    state: CollaborationState
) -> dict:
    """
    审核 Synthesizer 生成的答案草稿，
    返回结构化 ReviewResult。
    """

    monitor._emit(
        "collaboration_reviewer_start",
        "Reviewer 开始审核答案草稿",
        {}
    )

    query = state["query"]
    plan = state["plan"]
    draft_answer = state["draft_answer"]

    worker_results = state.get(
        "worker_results",
        []
    )

    worker_sections = []

    for result in worker_results:
        if result.success:
            section = f"""
任务编号：{result.task_id}
执行智能体：{result.agent}
状态：成功

结果：
{result.content}
"""
        else:
            section = f"""
任务编号：{result.task_id}
执行智能体：{result.agent}
状态：失败

错误：
{result.error}
"""

        worker_sections.append(section)

    worker_context = "\n\n".join(
        worker_sections
    )

    format_instructions = (
        reviewer_parser.get_format_instructions()
    )

    response = await model.ainvoke(
        [
            SystemMessage(
                content=f"""
{REVIEWER_SYSTEM_PROMPT}

你必须严格按照下面指定的 JSON 格式返回审核结果。

字段名称必须完全一致：
- passed
- score
- feedback
- missing_items
- retry_tasks

禁止使用其他字段名。

{format_instructions}

只返回合法 JSON，
不要输出 JSON 之外的任何文字。
"""
            ),
            HumanMessage(
                content=f"""
请审核下面这次多智能体协作任务。


【用户原始问题】

{query}


【Planner 总体目标】

{plan.goal}


【各 Worker 执行结果】

{worker_context}


【Synthesizer 答案草稿】

{draft_answer}


请判断当前答案是否已经达到最终交付标准。
"""
            ),
        ]
    )

    content = getattr(
        response,
        "content",
        ""
    )

    if not isinstance(content, str):
        content = str(content)

    review = reviewer_parser.parse(
        content
    )

    monitor._emit(
        "collaboration_review_done",
        (
            f"Reviewer 审核完成，"
            f"score={review.score:.2f}，"
            f"passed={review.passed}"
        ),
        {
            "passed": review.passed,
            "score": review.score,
            "feedback": review.feedback,
            "missing_items": review.missing_items,
            "retry_task_count": len(
                review.retry_tasks
            ),
        }
    )

    return {
        "review": review
    }