import operator

from typing import Annotated
from typing_extensions import TypedDict

from agent.collaboration.schemas import (
    TaskPlan,
    WorkerResult,
    ReviewResult,
)


class CollaborationState(TypedDict, total=False):
    """多智能体协作工作流共享状态。"""

    # 用户原始问题
    query: str

    # Planner 生成的任务计划
    plan: TaskPlan

    # 多个 Worker 的执行结果
    worker_results: Annotated[
        list[WorkerResult],
        operator.add
    ]

    # Synthesizer 生成的综合答案草稿
    draft_answer: str

    # Reviewer 审核结果
    review: ReviewResult

    # 已经执行了几轮自动补救
    retry_count: int

    # 最终交付给用户的答案
    final_answer: str