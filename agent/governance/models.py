from enum import Enum

from pydantic import BaseModel

from datetime import datetime
from typing import Any

from pydantic import Field


class RiskLevel(str, Enum):
    """
    Tool 操作风险等级。
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SQLPolicyResult(BaseModel):
    """
    SQL Governance 风险判断结果。
    """

    operation: str

    risk_level: RiskLevel

    requires_approval: bool

    reason: str

    # 修改：审批任务当前状态
class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    RESUMING = "RESUMING"


# 修改：一条真正等待人工处理的审批任务
class PendingApproval(BaseModel):
    """
    当前正在等待人工审批的 Tool Call。
    """

    thread_id: str

    tool_name: str

    operation: str

    risk_level: RiskLevel

    reason: str

    args: dict[str, Any]

    allowed_decisions: list[str] = Field(
        default_factory=lambda: [
            "approve",
            "reject",
        ]
    )

    status: ApprovalStatus = (
        ApprovalStatus.PENDING
    )

    created_at: datetime = Field(
        default_factory=datetime.now
    )