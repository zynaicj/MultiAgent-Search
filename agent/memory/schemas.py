from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    """
    长期记忆的数据结构。

    第一版只保留真正需要的字段，
    后续做向量检索时再增加 embedding。
    """

    memory_type: str = Field(
        description=(
            "记忆类型，例如 project_fact、"
            "preference、decision"
            
        )
    )

    content: str = Field(
        min_length=1,
        description="实际需要长期保存的记忆内容"
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )