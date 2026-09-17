from pathlib import Path

from agent.collaboration.workflow import collaboration_graph
from agent.memory.runtime import (
    recall_memory_context,
    remember_user_input,
)
from api.context import (
    reset_session_context,
    set_session_context,
    set_thread_context,
)
from api.monitor import monitor


project_root_path = Path(__file__).parents[2].resolve()


COLLABORATION_MEMORY_RUNTIME_CONTEXT = (
    "当前运行模式：多 Agent 协作模式。"
    "最终答案由多 Agent 协作工作流中的 "
    "Synthesizer 综合生成。"
)


async def run_collaboration_agent(
    task_query: str,
    session_id: str,
):
    """
    多智能体协作工作流的统一运行入口。

    负责：
    1. 创建当前任务工作目录
    2. 设置 session/thread ContextVar
    3. 根据用户原始问题和运行模式召回长期记忆
    4. 将原始 query 和 memory_context 分开传入 Graph
    5. 运行 collaboration_graph
    6. 将最终结果通过 Monitor 推送
    7. 从用户原始输入中提取并管理长期记忆
    8. 清理 ContextVar
    """

    print(
        "当前会话的 collaboration agent "
        f"开始执行！会话id:{session_id}"
    )

    session_dir = (
        project_root_path
        / "output"
        / f"session_{session_id}"
    )

    session_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    session_dir_str = str(
        session_dir
    ).replace(
        "\\",
        "/",
    )

    session_dir_token = set_session_context(
        session_dir_str
    )

    session_id_token = set_thread_context(
        session_id
    )

    try:
        monitor.report_session_dir(
            session_dir_str
        )

        memory_context = await recall_memory_context(
            user_query=task_query,
            runtime_context=(
                COLLABORATION_MEMORY_RUNTIME_CONTEXT
            ),
        )

        result = await collaboration_graph.ainvoke(
            {
                "query": task_query,
                "memory_context": memory_context,
                "retry_count": 0,
            }
        )

        final_answer = result.get(
            "final_answer",
            "",
        )

        if not final_answer:
            monitor._emit(
                "error",
                (
                    "多智能体协作工作流执行完成，"
                    "但没有生成 final_answer。"
                ),
            )

            return result

        print(
            "多智能体协作最终结果："
            f"{final_answer[:100]}"
        )

        monitor.report_task_result(
            final_answer
        )

        return result

    except Exception as e:
        monitor._emit(
            "error",
            (
                "多智能体协作工作流执行异常："
                f"{str(e)}"
            ),
        )

        raise

    finally:
        await remember_user_input(
            task_query
        )

        reset_session_context(
            session_dir_token,
            session_id_token,
        )