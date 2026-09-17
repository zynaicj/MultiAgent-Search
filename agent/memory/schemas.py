from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


MemoryType = Literal[
    "user_profile",
    "preference",
    "project_fact",
    "decision",
]


class MemoryCandidate(BaseModel):
    """
    LLM 从用户输入中提取出来的一条候选长期记忆。

    这里只表示“提取结果”，
    还没有真正写入 MongoDB。
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

    一条用户输入可能：
    1. 不包含长期记忆 -> memories=[]
    2. 包含一条长期记忆
    3. 同时包含多条长期记忆
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