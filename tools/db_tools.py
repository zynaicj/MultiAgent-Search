import os

from dotenv import load_dotenv
from mysql.connector import connect, Error
from langchain_core.tools import tool
from langgraph.types import interrupt

from api.monitor import monitor

from api.context import (
    get_thread_context,
)

from agent.governance import (
    PendingApproval,
    approval_registry,
    evaluate_sql_policy,
)

# 修改：
# Tool Governance 审计日志
from agent.governance.audit import (
    AuditEventType,
    audit_logger,
)


load_dotenv()


def get_db_config():
    """
    从环境变量读取数据库配置。
    """

    config = {
        "host": os.getenv(
            "MYSQL_HOST",
            "localhost",
        ),

        "port": int(
            os.getenv(
                "MYSQL_PORT",
                "3306",
            )
        ),

        "user": os.getenv(
            "MYSQL_USER"
        ),

        "password": os.getenv(
            "MYSQL_PASSWORD"
        ),

        "database": os.getenv(
            "MYSQL_DATABASE"
        ),

        "charset": os.getenv(
            "MYSQL_CHARSET",
            "utf8mb4",
        ),

        "collation": os.getenv(
            "MYSQL_COLLATION",
            "utf8mb4_unicode_ci",
        ),

        "autocommit": True,

        "connection_timeout": int(
            os.getenv(
                "MYSQL_TIMEOUT",
                "10",
            )
        ),

        "sql_mode": os.getenv(
            "MYSQL_SQL_MODE",
            "TRADITIONAL",
        ),
    }


    config = {
        key: value
        for key, value
        in config.items()
        if value is not None
    }


    required_keys = [
        "user",
        "password",
        "database",
    ]


    missing_keys = [
        key
        for key
        in required_keys
        if key not in config
    ]


    if missing_keys:

        raise ValueError(
            "缺失数据库核心配置："
            + ", ".join(
                missing_keys
            )
        )


    return config


@tool
def list_sql_tables() -> str:
    """
    查询当前数据库中的所有表。

    主要作用：
    让数据库 Agent 先了解当前有哪些表，
    方便后续进行表结构查询和 SQL 查询。
    """

    monitor.report_tool(
        tool_name=(
            "数据库表名查询工具："
            "list_sql_tables"
        ),
        args={},
    )


    config = (
        get_db_config()
    )


    try:

        with connect(
            **config
        ) as conn:

            with conn.cursor() as cursor:

                sql = (
                    "show tables"
                )


                cursor.execute(
                    sql
                )


                tables = (
                    cursor.fetchall()
                )


                if not tables:

                    return (
                        "没有可用的表"
                    )


                table_names = [
                    table[0]
                    for table
                    in tables
                ]


                return (
                    "可用的表有："
                    + ", ".join(
                        table_names
                    )
                )


    except Error as e:

        return (
            "查询出现异常："
            f"{str(e)}"
        )


@tool
def get_table_data(
    table_name,
) -> str:
    """
    查询指定表的数据。

    当前工具调用前，
    应优先调用 list_sql_tables 确认表名。

    该工具可以：
    1. 查询单表数据；
    2. 为复杂 SQL 提供字段和数据格式参考。
    """

    monitor.report_tool(
        tool_name=(
            "数据库表数据查询工具："
            "get_table_data"
        ),

        args={
            "table_name":
                table_name
        },
    )


    config = (
        get_db_config()
    )


    try:

        with connect(
            **config
        ) as conn:

            with conn.cursor() as cursor:

                sql = (
                    f"select * "
                    f"from {table_name} "
                    "limit 100"
                )


                cursor.execute(
                    sql
                )


                description = (
                    cursor.description
                )


                if not description:

                    return (
                        f"数据表："
                        f"{table_name}"
                        "为空没有数据！"
                    )


                columns = [
                    desc[0]
                    for desc
                    in description
                ]


                rows = (
                    cursor.fetchall()
                )


                results = [
                    ",".join(
                        map(
                            str,
                            row,
                        )
                    )
                    for row
                    in rows
                ]


                header_str = (
                    ",".join(
                        columns
                    )
                )


                data_str = (
                    "\n".join(
                        results
                    )
                )


                return (
                    f"{header_str}\n"
                    f"{data_str}"
                )


    except Error as e:

        return (
            "查询出现异常："
            f"{str(e)}"
        )


@tool
def execute_sql_query(
    query,
) -> str:
    """
    执行自定义 SQL。

    当前完整治理链路：

    SQL
        ↓
    Risk Policy
        ↓
    Audit
        ↓
    LOW 自动执行

    HIGH / CRITICAL
        ↓
    PendingApproval
        ↓
    interrupt
        ↓
    人工 approve / reject
        ↓
    Audit
        ↓
    approve 后才允许真正执行 SQL
    """

    monitor.report_tool(
        tool_name=(
            "数据库表数据查询工具："
            "execute_sql_query"
        ),

        args={
            "query":
                query
        },
    )


    # ======================================================
    # 修改 1：
    # SQL Governance 风险判断
    # ======================================================

    policy_result = (
        evaluate_sql_policy(
            query
        )
    )


    # 当前真实 Agent Thread。
    thread_id = (
        get_thread_context()
    )


    # LOW 风险场景即使是直接脚本调用，
    # 也允许执行。
    #
    # 如果没有 ContextVar，
    # Audit 中用 unknown 标记。
    audit_thread_id = (
        thread_id
        or "unknown"
    )


    # 后续 EXECUTED / FAILED
    # 可以知道是否经过人工批准。
    decision_type = None


    # ======================================================
    # 修改 2：
    # 高风险 SQL 进入 HITL
    # ======================================================

    if (
        policy_result
        .requires_approval
    ):

        # 高风险操作必须绑定真实 thread_id。
        # 没有 thread 就无法建立
        # PendingApproval / interrupt 上下文。
        if not thread_id:

            # 修改：
            # 即使最终 Fail Closed，
            # 也留下 Policy 审计记录。
            audit_logger.record(

                thread_id=(
                    audit_thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result.operation
                ),

                risk_level=(
                    policy_result.risk_level
                ),

                event_type=(
                    AuditEventType
                    .POLICY_EVALUATED
                ),

                requires_approval=True,

                reason=(
                    policy_result.reason
                ),

                args={
                    "query":
                        query
                },
            )


            # 修改：
            # 缺失审批上下文导致执行失败。
            audit_logger.record(

                thread_id=(
                    audit_thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result.operation
                ),

                risk_level=(
                    policy_result.risk_level
                ),

                event_type=(
                    AuditEventType
                    .FAILED
                ),

                requires_approval=True,

                reason=(
                    policy_result.reason
                ),

                args={
                    "query":
                        query
                },

                success=False,

                error=(
                    "缺少 thread_id，"
                    "无法建立人工审批上下文"
                ),
            )


            return (
                "该 SQL 操作未执行："
                "当前缺少 thread_id，"
                "无法建立人工审批上下文。"
            )


        # ==================================================
        # 创建待审批对象
        # ==================================================

        pending_approval = (
            PendingApproval(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result.operation
                ),

                risk_level=(
                    policy_result.risk_level
                ),

                reason=(
                    policy_result.reason
                ),

                args={
                    "query":
                        query
                },
            )
        )


        # ==================================================
        # 注册 Pending Approval
        # ==================================================

        try:

            (
                pending_approval,
                is_new,
            ) = (
                approval_registry
                .register(
                    pending_approval
                )
            )


        except RuntimeError as e:

            # 修改：
            # 当前请求虽然无法注册，
            # 但策略判断和失败原因
            # 仍然应该被审计。
            audit_logger.record(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result.operation
                ),

                risk_level=(
                    policy_result.risk_level
                ),

                event_type=(
                    AuditEventType
                    .POLICY_EVALUATED
                ),

                requires_approval=True,

                reason=(
                    policy_result.reason
                ),

                args={
                    "query":
                        query
                },
            )


            audit_logger.record(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result.operation
                ),

                risk_level=(
                    policy_result.risk_level
                ),

                event_type=(
                    AuditEventType
                    .FAILED
                ),

                requires_approval=True,

                reason=(
                    policy_result.reason
                ),

                args={
                    "query":
                        query
                },

                success=False,

                error=str(
                    e
                ),
            )


            return (
                "该 SQL 操作未执行："
                f"{str(e)}"
            )


        # ==================================================
        # interrupt / 前端审批 Payload
        # ==================================================

        approval_payload = {

            "type":
                "tool_approval",

            "thread_id":
                thread_id,

            "tool_name":
                pending_approval
                .tool_name,

            "operation":
                pending_approval
                .operation,

            "risk_level":
                pending_approval
                .risk_level
                .value,

            "reason":
                pending_approval
                .reason,

            "args":
                pending_approval
                .args,

            "allowed_decisions":
                pending_approval
                .allowed_decisions,
        }


        # ==================================================
        # 修改 3：
        # 只在第一次进入审批时记录：
        #
        # POLICY_EVALUATED
        # APPROVAL_REQUIRED
        #
        # Resume 后节点会重新执行，
        # register() 返回 is_new=False，
        # 因此不会产生重复 Audit。
        # ==================================================

        if is_new:

            audit_logger.record(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    pending_approval
                    .tool_name
                ),

                operation=(
                    pending_approval
                    .operation
                ),

                risk_level=(
                    pending_approval
                    .risk_level
                ),

                event_type=(
                    AuditEventType
                    .POLICY_EVALUATED
                ),

                requires_approval=True,

                reason=(
                    pending_approval
                    .reason
                ),

                args=(
                    pending_approval
                    .args
                ),
            )


            audit_logger.record(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    pending_approval
                    .tool_name
                ),

                operation=(
                    pending_approval
                    .operation
                ),

                risk_level=(
                    pending_approval
                    .risk_level
                ),

                event_type=(
                    AuditEventType
                    .APPROVAL_REQUIRED
                ),

                requires_approval=True,

                reason=(
                    pending_approval
                    .reason
                ),

                args=(
                    pending_approval
                    .args
                ),
            )


            monitor._emit(

                "tool_approval_required",

                (
                    "检测到高风险 SQL 操作："
                    f"{policy_result.operation}，"
                    "等待人工审批"
                ),

                approval_payload,
            )


        # ==================================================
        # interrupt：
        # 真正暂停 LangGraph
        # ==================================================

        decision = interrupt(
            approval_payload
        )


        # ==================================================
        # 解析审批结果
        # ==================================================

        if isinstance(
            decision,
            dict,
        ):

            decision_type = (
                decision.get(
                    "decision"
                )
            )

        else:

            decision_type = (
                str(
                    decision
                )
            )


        # ==================================================
        # 修改 4：
        # Reject → Audit REJECTED
        # ==================================================

        if (
            decision_type
            != "approve"
        ):

            audit_logger.record(

                thread_id=(
                    thread_id
                ),

                tool_name=(
                    "execute_sql_query"
                ),

                operation=(
                    policy_result
                    .operation
                ),

                risk_level=(
                    policy_result
                    .risk_level
                ),

                event_type=(
                    AuditEventType
                    .REJECTED
                ),

                requires_approval=True,

                reason=(
                    policy_result
                    .reason
                ),

                args={
                    "query":
                        query
                },

                decision=(
                    decision_type
                ),

                success=False,
            )


            monitor._emit(

                "tool_approval_rejected",

                (
                    f"{policy_result.operation} "
                    "操作已被人工拒绝"
                ),

                {
                    "thread_id":
                        thread_id,

                    "tool_name":
                        "execute_sql_query",

                    "operation":
                        policy_result
                        .operation,

                    "decision":
                        decision_type,
                },
            )


            approval_registry.clear(
                thread_id
            )


            return (
                "该 SQL 操作未执行："
                "人工审批未通过。"
                f"\nSQL：{query}"
            )


        # ==================================================
        # 修改 5：
        # Approve → Audit APPROVED
        # ==================================================

        audit_logger.record(

            thread_id=(
                thread_id
            ),

            tool_name=(
                "execute_sql_query"
            ),

            operation=(
                policy_result
                .operation
            ),

            risk_level=(
                policy_result
                .risk_level
            ),

            event_type=(
                AuditEventType
                .APPROVED
            ),

            requires_approval=True,

            reason=(
                policy_result
                .reason
            ),

            args={
                "query":
                    query
            },

            decision="approve",
        )


        monitor._emit(

            "tool_approval_approved",

            (
                f"{policy_result.operation} "
                "操作已获人工批准"
            ),

            {
                "thread_id":
                    thread_id,

                "tool_name":
                    "execute_sql_query",

                "operation":
                    policy_result
                    .operation,

                "decision":
                    "approve",
            },
        )


        approval_registry.clear(
            thread_id
        )


    # ======================================================
    # 修改 6：
    # LOW 风险没有 interrupt，
    # 因此这里直接记录一次 POLICY_EVALUATED。
    #
    # 高风险已经在 is_new 分支记录过，
    # 这里不能再次记录。
    # ======================================================

    else:

        audit_logger.record(

            thread_id=(
                audit_thread_id
            ),

            tool_name=(
                "execute_sql_query"
            ),

            operation=(
                policy_result
                .operation
            ),

            risk_level=(
                policy_result
                .risk_level
            ),

            event_type=(
                AuditEventType
                .POLICY_EVALUATED
            ),

            requires_approval=False,

            reason=(
                policy_result
                .reason
            ),

            args={
                "query":
                    query
            },
        )


    # ======================================================
    # 到达这里说明：
    #
    # 1. LOW 风险；
    # 或
    # 2. HIGH / CRITICAL 已被人工 approve。
    #
    # 现在才允许真正访问数据库。
    # ======================================================

    try:

        config = (
            get_db_config()
        )


        with connect(
            **config
        ) as conn:

            with conn.cursor() as cursor:

                cursor.execute(
                    query
                )


                description = (
                    cursor.description
                )


                # ==========================================
                # 修改 7：
                # SQL 成功执行，没有结果集
                # ==========================================

                if not description:

                    audit_logger.record(

                        thread_id=(
                            audit_thread_id
                        ),

                        tool_name=(
                            "execute_sql_query"
                        ),

                        operation=(
                            policy_result
                            .operation
                        ),

                        risk_level=(
                            policy_result
                            .risk_level
                        ),

                        event_type=(
                            AuditEventType
                            .EXECUTED
                        ),

                        requires_approval=(
                            policy_result
                            .requires_approval
                        ),

                        reason=(
                            policy_result
                            .reason
                        ),

                        args={
                            "query":
                                query
                        },

                        decision=(
                            decision_type
                        ),

                        success=True,
                    )


                    return (
                        "SQL 已执行完成，"
                        "但没有返回结果集。"
                        f"\nSQL：{query}"
                    )


                columns = [
                    desc[0]
                    for desc
                    in description
                ]


                rows = (
                    cursor.fetchall()
                )


                results = [
                    ",".join(
                        map(
                            str,
                            row,
                        )
                    )
                    for row
                    in rows
                ]


                header_str = (
                    ",".join(
                        columns
                    )
                )


                data_str = (
                    "\n".join(
                        results
                    )
                )


                # ==========================================
                # 修改 8：
                # SQL 查询完整成功
                # ==========================================

                audit_logger.record(

                    thread_id=(
                        audit_thread_id
                    ),

                    tool_name=(
                        "execute_sql_query"
                    ),

                    operation=(
                        policy_result
                        .operation
                    ),

                    risk_level=(
                        policy_result
                        .risk_level
                    ),

                    event_type=(
                        AuditEventType
                        .EXECUTED
                    ),

                    requires_approval=(
                        policy_result
                        .requires_approval
                    ),

                    reason=(
                        policy_result
                        .reason
                    ),

                    args={
                        "query":
                            query
                    },

                    decision=(
                        decision_type
                    ),

                    success=True,
                )


                return (
                    f"{header_str}\n"
                    f"{data_str}"
                )


    except (
        Error,
        ValueError,
    ) as e:

        # ==================================================
        # 修改 9：
        # 数据库连接 / SQL 执行失败
        # ==================================================

        audit_logger.record(

            thread_id=(
                audit_thread_id
            ),

            tool_name=(
                "execute_sql_query"
            ),

            operation=(
                policy_result
                .operation
            ),

            risk_level=(
                policy_result
                .risk_level
            ),

            event_type=(
                AuditEventType
                .FAILED
            ),

            requires_approval=(
                policy_result
                .requires_approval
            ),

            reason=(
                policy_result
                .reason
            ),

            args={
                "query":
                    query
            },

            decision=(
                decision_type
            ),

            success=False,

            error=str(
                e
            ),
        )


        return (
            "查询出现异常："
            f"{str(e)}"
        )


if __name__ == "__main__":

    print(
        "db_tools.py loaded"
    )