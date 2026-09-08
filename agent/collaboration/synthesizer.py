from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from agent.llm import model
from agent.collaboration.state import (
    CollaborationState,
)
from api.monitor import monitor


SYNTHESIZER_SYSTEM_PROMPT = """
你是多智能体协作系统中的结果综合智能体 Synthesizer。

你的职责是：

根据用户原始问题、Planner 的任务计划，
以及多个专业 Worker 返回的结果，
生成一个统一、完整、逻辑清晰的最终答案草稿。

要求：

1. 必须围绕用户原始问题进行回答。

2. 综合不同 Worker 的信息，而不是简单把多个结果机械拼接。

3. 如果不同 Worker 的结果存在重复内容，应进行归纳和去重。

4. 如果多个 Worker 的结果之间存在关联，应进行交叉分析，
   而不是彼此独立罗列。

5. 不允许编造 Worker 没有提供的信息。

6. 如果某个 Worker 执行失败，应明确考虑当前缺失的信息，
   不要假装该信息已经获取成功。

7. 当前阶段只生成答案草稿，
   不负责 Reviewer 审核，也不负责重新执行失败任务。

8. 输出自然语言答案即可，不需要 JSON。

9. 如果 Worker 结果中既存在之前的失败记录，
   又存在后续 Retry 成功获得的补充结果，
   应优先使用后续成功结果修正答案，
   不要继续把已经被 Retry 修复的历史失败当作最终结论。
"""


async def synthesizer_node(
    state: CollaborationState
) -> dict:
    """
    综合多个 Worker 的执行结果，
    生成统一答案草稿。
    """
    monitor._emit(
        "collaboration_synthesizer_start",
        "Synthesizer 开始综合多个 Worker 的结果",
        {
            "worker_result_count": len(
                state.get(
                    "worker_results",
                    []
                )
            )
        }
    )

    query = state["query"]
    plan = state["plan"]

    worker_results = state.get(
        "worker_results",
        []
    )

    result_sections = []

    for result in worker_results:
        if result.success:
            section = f"""
任务编号：{result.task_id}
执行智能体：{result.agent}
执行状态：成功

执行结果：
{result.content}
"""
        else:
            section = f"""
任务编号：{result.task_id}
执行智能体：{result.agent}
执行状态：失败

错误信息：
{result.error}
"""

        result_sections.append(section)

    worker_context = "\n\n".join(
        result_sections
    )

    response = await model.ainvoke(
        [
            SystemMessage(
                content=SYNTHESIZER_SYSTEM_PROMPT
            ),
            HumanMessage(
                content=f"""
用户原始问题：

{query}


Planner 的总体目标：

{plan.goal}


各 Worker 执行结果：

{worker_context}


请根据以上信息生成统一、完整的答案草稿。
"""
            ),
        ]
    )

    content = getattr(
        response,
        "content",
        ""
    )

    monitor._emit(
        "collaboration_synthesizer_done",
        "Synthesizer 已生成答案草稿",
        {}
    )

    if not isinstance(content, str):
        content = str(content)

    return {
        "draft_answer": content
    }