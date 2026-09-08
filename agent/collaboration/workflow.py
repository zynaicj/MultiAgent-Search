from langgraph.graph import (
    StateGraph,
    START,
    END,
)
from langgraph.types import Send

from agent.collaboration.state import (
    CollaborationState,
)
from agent.collaboration.planner import (
    planner_node,
)
from agent.collaboration.workers import (
    worker_node,
)
from agent.collaboration.synthesizer import (
    synthesizer_node,
)
from agent.collaboration.reviewer import (
    reviewer_node,
)


# 最多允许 Reviewer 驱动两轮自动补充
MAX_RETRY_ROUNDS = 2


def dispatch_workers(
    state: CollaborationState
):
    """
    第一次执行：
    把 Planner 生成的任务动态分发给 Worker。
    """

    plan = state["plan"]

    return [
        Send(
            "worker",
            {
                "task": task
            }
        )
        for task in plan.tasks
    ]


async def prepare_retry_node(
    state: CollaborationState
) -> dict:
    """
    开始下一轮 Retry 前，
    将 retry_count 加 1。
    """

    current_retry_count = state.get(
        "retry_count",
        0
    )

    return {
        "retry_count": current_retry_count + 1
    }


def dispatch_retry_workers(
    state: CollaborationState
):
    """
    把 Reviewer 生成的 retry_tasks
    动态重新分发给 Worker。
    """

    review = state["review"]

    return [
        Send(
            "worker",
            {
                "task": task
            }
        )
        for task in review.retry_tasks
    ]


def route_after_review(
    state: CollaborationState
) -> str:
    """
    Reviewer 完成后决定：
    结束，还是进入下一轮 Retry。
    """

    review = state["review"]

    retry_count = state.get(
        "retry_count",
        0
    )

    # 审核已经通过
    if review.passed:
        return "finalize"

    # 已经达到最大重试轮数
    if retry_count >= MAX_RETRY_ROUNDS:
        return "finalize"

    # Reviewer 判断不通过，
    # 但没有生成任何可补充任务
    if not review.retry_tasks:
        return "finalize"

    # 还有补救任务，可以继续执行
    return "prepare_retry"


async def finalize_node(
    state: CollaborationState
) -> dict:
    """
    将最后一轮 Synthesizer 生成的草稿
    设置为最终答案。
    """

    return {
        "final_answer": state.get(
            "draft_answer",
            ""
        )
    }


builder = StateGraph(
    CollaborationState
)


builder.add_node(
    "planner",
    planner_node
)

builder.add_node(
    "worker",
    worker_node
)

builder.add_node(
    "synthesizer",
    synthesizer_node
)

builder.add_node(
    "reviewer",
    reviewer_node
)

builder.add_node(
    "prepare_retry",
    prepare_retry_node
)

builder.add_node(
    "finalize",
    finalize_node
)


builder.add_edge(
    START,
    "planner"
)


# Planner 根据任务数量动态创建 Worker
builder.add_conditional_edges(
    "planner",
    dispatch_workers,
    [
        "worker"
    ]
)


# 所有并行 Worker 完成后进入 Synthesizer
builder.add_edge(
    "worker",
    "synthesizer"
)


builder.add_edge(
    "synthesizer",
    "reviewer"
)


# Reviewer 决定结束还是 Retry
builder.add_conditional_edges(
    "reviewer",
    route_after_review,
    {
        "prepare_retry": "prepare_retry",
        "finalize": "finalize",
    }
)


# Retry 时重新动态创建指定 Worker
builder.add_conditional_edges(
    "prepare_retry",
    dispatch_retry_workers,
    [
        "worker"
    ]
)


builder.add_edge(
    "finalize",
    END
)


collaboration_graph = builder.compile()