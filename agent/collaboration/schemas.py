from typing import Literal

from pydantic import BaseModel, Field


AgentName = Literal[
    "市场情报助手",
    "数据分析助手",
    "内部知识助手",
]


class PlannedTask(BaseModel):
    """Planner 拆解出的一个子任务。"""

    task_id: str = Field(
        description="子任务唯一标识，例如 task_1"
    )

    agent: AgentName = Field(
        description="负责执行该任务的专业智能体"
    )

    description: str = Field(
        description="需要该智能体完成的具体任务"
    )

    expected_output: str = Field(
        description="该任务期望返回的信息"
    )

    depends_on: list[str] = Field(
        default_factory=list,
        description="该任务依赖的其他 task_id"
    )


class TaskPlan(BaseModel):
    """Planner 为复杂用户问题生成的完整任务计划。"""

    goal: str = Field(
        description="对用户最终目标的简要描述"
    )

    tasks: list[PlannedTask] = Field(
        min_length=1,
        description="需要执行的子任务列表"
    )


class WorkerResult(BaseModel):
    """一个 Worker 完成任务后返回的标准结果。"""

    task_id: str

    agent: AgentName

    success: bool = True

    content: str = ""

    error: str | None = None


class ReviewResult(BaseModel):
    """Reviewer 对综合答案的审核结果。"""

    passed: bool = Field(
        description="当前答案是否达到最终交付标准"
    )

    score: float = Field(
        ge=0,
        le=1,
        description="0~1 的质量评分"
    )

    feedback: str = Field(
        description="Reviewer 的总体评价"
    )

    missing_items: list[str] = Field(
        default_factory=list,
        description="当前答案缺失的信息"
    )

    retry_tasks: list[PlannedTask] = Field(
        default_factory=list,
        description="需要重新执行或补充执行的任务"
    )