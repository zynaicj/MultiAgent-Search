import os

from dotenv import load_dotenv
from mysql.connector import connect, Error
from langchain_core.tools import tool
from langgraph.types import interrupt

from api.monitor import monitor

# 修改：获取当前 Agent 任务对应的 thread_id
from api.context import get_thread_context

# 修改：SQL 风险策略 + Pending Approval 注册表
from agent.governance import (
    PendingApproval,
    approval_registry,
    evaluate_sql_policy,
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

    # 去掉 None 配置
    config = {
        key: value
        for key, value in config.items()
        if value is not None
    }

    # 校验数据库核心配置
    required_keys = [
        "user",
        "password",
        "database",
    ]

    missing_keys = [
        key
        for key in required_keys
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

    config = get_db_config()

    try:

        with connect(
            **config
        ) as conn:

            with conn.cursor() as cursor:

                sql = "show tables"

                cursor.execute(
                    sql
                )

                tables = (
                    cursor.fetchall()
                )

                if not tables:
                    return "没有可用的表"

                table_names = [
                    table[0]
                    for table in tables
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

    config = get_db_config()

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
                    for desc in description
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
                    for row in rows
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

    在真正访问数据库之前，
    会先经过 Tool Governance 风险判断。

    LOW：
        自动执行。

    HIGH / CRITICAL：
        创建 PendingApproval，
        通过 interrupt 暂停 Graph，
        等待人工批准或拒绝。
    """

    # 先告诉前端当前调用了 SQL Tool
    monitor.report_tool(
        tool_name=(
            "数据库表数据查询工具："
            "execute_sql_query"
        ),
        args={
            "query": query
        },
    )


    # ======================================================
    # 修改：第一步，SQL Governance 风险判断
    # ======================================================

    policy_result = (
        evaluate_sql_policy(
            query
        )
    )


    # ======================================================
    # 修改：第二步，高风险 SQL 进入 HITL
    # ======================================================

    if policy_result.requires_approval:

        # 当前 Tool Call 必须绑定一个真实 thread_id
        thread_id = (
            get_thread_context()
        )

        if not thread_id:

            return (
                "该 SQL 操作未执行："
                "当前缺少 thread_id，"
                "无法建立人工审批上下文。"
            )


        # ==================================================
        # 修改：创建真正的待审批对象
        # ==================================================

        pending_approval = (
            PendingApproval(
                thread_id=thread_id,

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
                    "query": query
                },
            )
        )


        # ==================================================
        # 修改：注册到 ApprovalRegistry
        # ==================================================

        try:

            (
                pending_approval,
                is_new,
            ) = (
                approval_registry.register(
                    pending_approval
                )
            )

        except RuntimeError as e:

            return (
                "该 SQL 操作未执行："
                f"{str(e)}"
            )


        # ==================================================
        # 修改：构造 interrupt / 前端审批数据
        # ==================================================

        approval_payload = {
            "type":
                "tool_approval",

            "thread_id":
                thread_id,

            "tool_name":
                pending_approval.tool_name,

            "operation":
                pending_approval.operation,

            "risk_level":
                pending_approval
                .risk_level
                .value,

            "reason":
                pending_approval.reason,

            "args":
                pending_approval.args,

            "allowed_decisions":
                pending_approval
                .allowed_decisions,
        }


        # ==================================================
        # 修改：
        # 只有第一次注册审批时才通知前端
        #
        # interrupt Resume 后节点会重新执行，
        # register() 会发现同一审批已经存在，
        # 此时 is_new=False，
        # 因而不会重复推送审批卡片。
        # ==================================================

        if is_new:

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
        # 修改：真正暂停 LangGraph
        # ==================================================

        decision = interrupt(
            approval_payload
        )


        # ==================================================
        # 修改：解析人工审批结果
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

            decision_type = str(
                decision
            )


        # ==================================================
        # 修改：不是 approve，一律拒绝
        # Fail Closed
        # ==================================================

        if (
            decision_type
            != "approve"
        ):

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


            # 修改：
            # 本次审批已经被消费，
            # 从 Registry 中删除。
            approval_registry.clear(
                thread_id
            )


            return (
                "该 SQL 操作未执行："
                "人工审批未通过。"
                f"\nSQL：{query}"
            )


        # ==================================================
        # 修改：人工明确 approve
        # ==================================================

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


        # 修改：
        # approve 已经被 interrupt 消费，
        # 清除 Pending Approval。
        approval_registry.clear(
            thread_id
        )


    # ======================================================
    # 只有下面两种情况能够执行到这里：
    #
    # 1. LOW 风险 SQL
    # 2. HIGH / CRITICAL SQL 并且人工 approve
    # ======================================================

    config = get_db_config()


    try:

        with connect(
            **config
        ) as conn:

            with conn.cursor() as cursor:

                # 真正执行 SQL
                cursor.execute(
                    query
                )


                description = (
                    cursor.description
                )


                # INSERT / UPDATE / DELETE 等操作
                # 通常没有查询结果集
                if not description:

                    return (
                        "SQL 已执行完成，"
                        "但没有返回结果集。"
                        f"\nSQL：{query}"
                    )


                columns = [
                    desc[0]
                    for desc in description
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
                    for row in rows
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


if __name__ == "__main__":

    print(
        "db_tools.py loaded"
    )