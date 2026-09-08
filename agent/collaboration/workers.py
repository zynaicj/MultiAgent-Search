from deepagents import create_deep_agent

from agent.llm import model

from agent.subagents.network_search_agent import (
    network_search_agent,
)
from agent.subagents.database_query_agent import (
    database_query_agent,
)
from agent.subagents.knowledge_base_agent import (
    knowledge_base_agent,
)

from agent.collaboration.schemas import (
    PlannedTask,
    WorkerResult,
)

from typing_extensions import TypedDict
from api.monitor import monitor


# 三个现有专业 Agent 的配置
WORKER_CONFIGS = {
    network_search_agent["name"]: network_search_agent,
    database_query_agent["name"]: database_query_agent,
    knowledge_base_agent["name"]: knowledge_base_agent,
}


def _create_worker_agent(worker_config: dict):
    """
    根据现有 SubAgent 配置，
    创建一个可以独立运行的 Worker Agent。
    """

    return create_deep_agent(
        model=model,
        system_prompt=worker_config["system_prompt"],
        tools=worker_config["tools"],
    )


# 程序启动时创建三个可独立执行的 Worker
WORKER_AGENTS = {
    worker_name: _create_worker_agent(worker_config)
    for worker_name, worker_config
    in WORKER_CONFIGS.items()
}


class WorkerState(TypedDict):
    """单个并行 Worker 接收到的临时状态。"""

    task: PlannedTask


async def worker_node(
    state: WorkerState
) -> dict:
    """
    执行 Planner 分配的一个子任务。
    """

    task = state["task"]

    monitor._emit(
        "collaboration_worker_start",
        f"{task.agent} 开始执行 {task.task_id}",
        {
            "task_id": task.task_id,
            "agent": task.agent,
            "description": task.description,
        }
    )

    worker_agent = WORKER_AGENTS.get(
        task.agent
    )

    if worker_agent is None:
        return {
            "worker_results": [
                WorkerResult(
                    task_id=task.task_id,
                    agent=task.agent,
                    success=False,
                    error=f"未找到 Worker：{task.agent}",
                )
            ]
        }

    worker_prompt = f"""
Planner 为你分配了下面这个独立子任务。

任务编号：
{task.task_id}

任务内容：
{task.description}

期望输出：
{task.expected_output}

请只完成这个子任务。
根据你的专业能力调用必要工具获取真实信息，
不要扩展到与当前任务无关的内容。

完成后直接给出清晰、完整的任务结果。
"""

    try:
        result = await worker_agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": worker_prompt,
                    }
                ]
            }
        )

        messages = result.get(
            "messages",
            []
        )

        if not messages:
            raise RuntimeError(
                "Worker 未返回任何消息"
            )

        final_message = messages[-1]

        content = getattr(
            final_message,
            "content",
            ""
        )

        if not isinstance(content, str):
            content = str(content)

        worker_result = WorkerResult(
            task_id=task.task_id,
            agent=task.agent,
            success=True,
            content=content,
        )
        monitor._emit(
            "collaboration_worker_done",
            f"{task.agent} 已完成 {task.task_id}",
            {
                "task_id": task.task_id,
                "agent": task.agent,
                "success": True,
            }
        )

        return {
            "worker_results": [
                worker_result
            ]
        }

    except Exception as e:

        monitor._emit(
            "collaboration_worker_failed",
            f"{task.agent} 执行 {task.task_id} 失败",
            {
                "task_id": task.task_id,
                "agent": task.agent,
                "success": False,
                "error": str(e),
            }
        )
        return {
            "worker_results": [
                WorkerResult(
                    task_id=task.task_id,
                    agent=task.agent,
                    success=False,
                    error=str(e),
                )
            ]
        }