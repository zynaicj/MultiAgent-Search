from enum import Enum

from pydantic import BaseModel


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