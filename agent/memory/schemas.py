from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


MemoryType = Literal[
    "user_profile",
    "preference",
    "project_fact",
    "decision",
]

MemoryAction = Literal[
    "add",
    "skip",
    "update",
]


class MemoryCandidate(BaseModel):
    """
    LLM 从用户输入中提取出来的一条候选长期记忆。
    """

    memory_type: MemoryType = Field(
        description=(
            "长期记忆类型："
            "user_profile 表示稳定的用户信息；"
            "preference 表示用户长期偏好；"
            "project_fact 表示项目中的稳定事实；"
            "decision 表示用户已经明确做出的长期决定"
        )
    )

    content: str = Field(
        min_length=1,
        description="整理后的、可独立理解的长期记忆内容",
    )


class MemoryExtractionResult(BaseModel):
    """
    一次长期记忆提取的结构化结果。
    """

    memories: list[MemoryCandidate] = Field(
        default_factory=list,
        description="从本轮用户输入中提取出的长期记忆；没有则返回空列表",
    )


class MemoryRecord(BaseModel):
    """
    真正写入 MongoDB 的长期记忆数据结构。
    """

    memory_type: MemoryType

    content: str = Field(
        min_length=1,
        description="实际需要长期保存的记忆内容",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemoryManagementDecision(BaseModel):
    """
    Memory Manager 对一条新记忆做出的管理决策。
    """

    action: MemoryAction = Field(
        description=(
            "add 表示新增；"
            "skip 表示与已有记忆重复；"
            "update 表示新记忆应该替换某条旧记忆"
        )
    )

    target_memory_id: str | None = Field(
        default=None,
        description="skip 或 update 对应的已有 MongoDB 记忆 ID；add 时为空",
    )

    final_content: str = Field(
        min_length=1,
        description="执行操作后应该保留的最终长期记忆内容",
    )

    reason: str = Field(
        min_length=1,
        description="做出当前记忆管理决策的简短原因",
    )


class MemoryProcessResult(BaseModel):
    """
    Service 层执行完记忆管理后的结果。
    """

    action: MemoryAction
    memory_id: str | None
    memory_type: MemoryType
    content: str
    reason: str