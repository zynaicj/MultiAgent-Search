import operator

from typing import Annotated
from typing_extensions import TypedDict

from agent.collaboration.schemas import (
    TaskPlan,
    WorkerResult,
    ReviewResult,
)


class CollaborationState(TypedDict, total=False):
    """
    多智能体协作工作流共享状态。
    """

    # 用户真正的原始问题。
    #
    # 注意：
    # 这里不能混入长期记忆上下文。
    query: str

    # 根据当前 query 召回出的长期记忆。
    #
    # 它只是辅助上下文，
    # 不能被当作用户本轮的新指令。
    memory_context: str

    # Planner 生成的任务计划
    plan: TaskPlan

    # 多个 Worker 的执行结果
    worker_results: Annotated[
        list[WorkerResult],
        operator.add,
    ]

    # Synthesizer 生成的综合答案草稿
    draft_answer: str

    # Reviewer 审核结果
    review: ReviewResult

    # 已经执行了几轮自动补救
    retry_count: int

    # 最终交付给用户的答案
    final_answer: str