from pathlib import Path

from agent.collaboration.workflow import (
    collaboration_graph,
)

from api.context import (
    set_session_context,
    set_thread_context,
    reset_session_context,
)

from api.monitor import monitor


project_root_path = (
    Path(__file__).parents[2].resolve()
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
    3. 运行 collaboration_graph
    4. 将最终结果通过 Monitor 推送
    5. 清理 ContextVar
    """

    print(
        f"当前会话的 collaboration agent "
        f"开始执行！会话id:{session_id}"
    )

    # 当前任务专属输出目录
    session_dir = (
        project_root_path
        / "output"
        / f"session_{session_id}"
    )

    session_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ContextVar 中目前保存的是字符串路径
    session_dir_str = str(
        session_dir
    ).replace("\\", "/")

    # 设置当前任务上下文
    session_dir_token = (
        set_session_context(
            session_dir_str
        )
    )

    session_id_token = (
        set_thread_context(
            session_id
        )
    )

    

    try:
        # 告诉前端当前任务的工作目录
        monitor.report_session_dir(
            session_dir_str
        )
        result = (
            await collaboration_graph.ainvoke(
                {
                    "query": task_query,
                    "retry_count": 0,
                }
            )
        )

        final_answer = result.get(
            "final_answer",
            ""
        )

        if not final_answer:
            monitor._emit(
                "error",
                "多智能体协作工作流执行完成，"
                "但没有生成 final_answer。",
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
            "多智能体协作工作流执行异常："
            f"{str(e)}",
        )

        raise

    finally:
        reset_session_context(
            session_dir_token,
            session_id_token,
        )