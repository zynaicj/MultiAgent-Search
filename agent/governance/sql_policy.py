import re

from agent.governance.models import (
    RiskLevel,
    SQLPolicyResult,
)


READ_ONLY_OPERATIONS = {
    "SELECT",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "EXPLAIN",
}


WRITE_OPERATIONS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
}


DDL_OPERATIONS = {
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "RENAME",
}


PRIVILEGE_OPERATIONS = {
    "GRANT",
    "REVOKE",
}


def _normalize_sql(query: str) -> str:
    """
    对 SQL 做基础清洗，方便后续识别。
    """

    query = query.strip()

    # 去除开头连续出现的 SQL 注释
    query = re.sub(
        r"^\s*(--[^\n]*\n\s*)+",
        "",
        query,
    )

    query = re.sub(
        r"^\s*/\*.*?\*/\s*",
        "",
        query,
        flags=re.DOTALL,
    )

    return query.strip()


def _extract_operation(query: str) -> str:
    """
    提取 SQL 的主要操作类型。
    """

    normalized_query = _normalize_sql(query)

    if not normalized_query:
        return "UNKNOWN"

    match = re.match(
        r"^([A-Za-z]+)",
        normalized_query,
    )

    if not match:
        return "UNKNOWN"

    return match.group(1).upper()


def _contains_multiple_statements(
    query: str,
) -> bool:
    """
    判断是否包含多条 SQL。

    末尾单独一个分号不算多语句。
    """

    normalized_query = query.strip()

    if normalized_query.endswith(";"):
        normalized_query = (
            normalized_query[:-1]
        )

    return ";" in normalized_query


def evaluate_sql_policy(
    query: str,
) -> SQLPolicyResult:
    """
    判断 SQL 风险等级以及是否需要人工审批。

    默认策略：
    1. 明确只读 SQL 自动允许；
    2. 数据写操作需要审批；
    3. DDL / 权限变更属于最高风险；
    4. 多 SQL 和无法识别的 SQL 默认进入审批。
    """

    if not isinstance(query, str):
        return SQLPolicyResult(
            operation="UNKNOWN",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            reason="SQL 必须是字符串，无法安全识别当前输入",
        )

    normalized_query = _normalize_sql(
        query
    )

    if not normalized_query:
        return SQLPolicyResult(
            operation="UNKNOWN",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            reason="SQL 为空，拒绝自动执行",
        )

    operation = _extract_operation(
        normalized_query
    )

    # 多语句默认认为风险较高
    if _contains_multiple_statements(
        normalized_query
    ):
        return SQLPolicyResult(
            operation=operation,
            risk_level=RiskLevel.CRITICAL,
            requires_approval=True,
            reason="检测到多条 SQL 语句，需要人工确认后才能执行",
        )

    # 明确只读
    if operation in READ_ONLY_OPERATIONS:
        return SQLPolicyResult(
            operation=operation,
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            reason=f"{operation} 属于只读数据库操作，可自动执行",
        )

    # 数据修改
    if operation in WRITE_OPERATIONS:
        return SQLPolicyResult(
            operation=operation,
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            reason=f"{operation} 会修改数据库数据，需要人工审批",
        )

    # 数据库结构修改
    if operation in DDL_OPERATIONS:
        return SQLPolicyResult(
            operation=operation,
            risk_level=RiskLevel.CRITICAL,
            requires_approval=True,
            reason=f"{operation} 会修改数据库结构，属于高风险操作",
        )

    # 权限相关
    if operation in PRIVILEGE_OPERATIONS:
        return SQLPolicyResult(
            operation=operation,
            risk_level=RiskLevel.CRITICAL,
            requires_approval=True,
            reason=f"{operation} 会修改数据库权限，需要人工审批",
        )

    # 对于目前没有明确识别的 SQL，
    # 采用默认拒绝自动执行的保守策略
    return SQLPolicyResult(
        operation=operation,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        reason=(
            f"无法确认 {operation} 是否属于安全只读操作，"
            "默认要求人工审批"
        ),
    )