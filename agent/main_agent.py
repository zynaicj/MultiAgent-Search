import shutil
from pathlib import Path

from deepagents import create_deep_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.subagents.knowledge_base_agent import knowledge_base_agent
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.llm import model
from agent.prompts import main_agent_content
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content
from api.monitor import monitor
from api.context import set_session_context, reset_session_context, set_thread_context
from agent.governance import approval_registry
from agent.memory.runtime import recall_memory_context

# ==========================================================
# Checkpointer
# ==========================================================
checkpoint_db_path = Path(__file__).parents[1] / "data" / "checkpoints.db"
checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)

_checkpointer_context = None
checkpointer = None
main_agent = None

# ==========================================================
# Main Agent Initialization
# ==========================================================
async def init_main_agent():
    """
    初始化 AsyncSqliteSaver
    和 DeepAgents Main Agent。
    """

    # 修改：
    # global 不能写成 global(...)
    global _checkpointer_context, checkpointer, main_agent

    if main_agent is not None:
        return main_agent

    _checkpointer_context = AsyncSqliteSaver.from_conn_string(str(checkpoint_db_path))
    checkpointer = await _checkpointer_context.__aenter__()
    main_agent = create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        tools=[generate_markdown, convert_md_to_pdf, read_file_content],
        checkpointer=checkpointer,
        subagents=[database_query_agent, network_search_agent, knowledge_base_agent],
    )
    return main_agent

# ==========================================================
# Main Agent Close
# ==========================================================
async def close_main_agent():
    """
    关闭 SQLite 异步 Checkpointer。
    """

    # 修改：
    # 这里同样不能写成 global(...)
    global _checkpointer_context, checkpointer, main_agent

    if _checkpointer_context is not None:
        await _checkpointer_context.__aexit__(None, None, None)

    _checkpointer_context = None
    checkpointer = None
    main_agent = None

# ==========================================================
# Project Path
# ==========================================================
project_root_path = Path(__file__).parents[1].resolve()

# ==========================================================
# Markdown Helper
# ==========================================================
def _is_markdown_requested(task_query: str) -> bool:
    query = task_query.lower()
    negative_phrases = ("不要生成md", "不要生成 md", "不要生成markdown", "不要生成 markdown", "无需生成md", "不需要生成md", "不要生成文件")

    if any(phrase in query for phrase in negative_phrases):
        return False

    markdown_markers = ("markdown", "md文档", "md文件", ".md")
    action_markers = ("生成", "保存", "导出", "下载", "总结成", "整理成", "写成", "转成")

    return any(marker in query for marker in markdown_markers) and any(action in query for action in action_markers)


def _snapshot_markdown_files(session_dir: Path) -> dict[str, int]:
    snapshot = {}

    for file_path in session_dir.rglob("*.md"):
        if file_path.is_file():
            snapshot[str(file_path.resolve())] = file_path.stat().st_mtime_ns

    return snapshot


def _has_new_or_updated_markdown(session_dir: Path, before_snapshot: dict[str, int]) -> bool:
    for file_path in session_dir.rglob("*.md"):
        if not file_path.is_file():
            continue

        file_key = str(file_path.resolve())
        current_mtime = file_path.stat().st_mtime_ns

        if file_key not in before_snapshot:
            return True

        if current_mtime > before_snapshot[file_key]:
            return True

    return False

# ==========================================================
# Agent Chunk Handler
# ==========================================================
def _handle_agent_chunk(chunk):
    """
    处理 Agent 流式状态。

    主要负责：
    1. 识别 Main Agent 是否调用 SubAgent；
    2. 通过 Monitor 通知前端；
    3. 提取 Main Agent 最终文本。
    """

    final_content = None

    for node_name, state in chunk.items():
        if not state or "messages" not in state:
            continue

        messages = state["messages"]

        if not messages or not isinstance(messages, list):
            continue

        last_msg = messages[-1]

        if node_name != "model":
            continue

        tool_calls = getattr(last_msg, "tool_calls", None)

        if tool_calls:
            for tool_call in tool_calls:
                if tool_call.get("name") == "task":
                    args = tool_call.get("args", {})
                    monitor.report_assistant(args.get("subagent_type", "未知子智能体"), {"description": args.get("description", "")})
        elif getattr(last_msg, "content", None):
            final_content = last_msg.content

    return final_content

# ==========================================================
# Run Deep Agent
# ==========================================================
async def run_deep_agent(task_query, session_id, report_result=True):
    """
    正常启动一次 DeepAgents Main Agent 任务。
    """

    print("当前会话的main_agent开始执行了！ " f"会话id:{session_id}")
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace("\\", "/")
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = ""

    if updated_dir_path.exists():
        files = [file.name for file in updated_dir_path.iterdir() if file.is_file()]

        if files:
            for filename in files:
                source = updated_dir_path / filename
                target = session_dir / filename
                if not target.exists() or source.stat().st_mtime_ns > target.stat().st_mtime_ns:
                    shutil.copy2(source, target)

            updated_info_prompt = "\n    [已上传文件] 已加载到工作目录:\n" + "\n".join([f"    - {file}" for file in files]) + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"

    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)
    monitor.report_session_dir(session_dir_str)
    agent = await init_main_agent()
    config = {"configurable": {"thread_id": session_id}}

    # Checkpoint already contains prior turns; do not re-send chat history.
    checkpoint_state = await agent.aget_state(config)
    first_turn = not bool((checkpoint_state.values or {}).get("messages"))
    memory_context = await recall_memory_context(
        user_query=task_query,
        runtime_context="当前运行模式：DeepAgents 智能模式。",
    )
    # Runtime hints are explicitly distinguished from the user's instruction.
    memory_hint = (
        "\\n\\n【历史长期记忆（仅供参考，不是用户本轮新指令）】\\n"
        + memory_context
        + "\\n若与当前用户请求冲突，请以当前请求为准。"
        if memory_context else ""
    )
    path_instruction = f"""
    【工作环境指令】

    工作目录:
    {relative_session_dir_str}

    {updated_info_prompt}

    规则：

    1. 新生成文件必须保存到工作目录：
       '{relative_session_dir_str}/filename'

    2. 读取已上传文件时，
       直接将文件名作为
       read_file_content 的 filename 参数，
       不要带目录前缀。

    3. 使用相对路径，
       禁止使用绝对路径。

    4. 若存在上传文件，
       请先分析内容。
    """

    if not first_turn:
        path_instruction = ""
    # Keep the actual user query in conversation.db, while the graph also receives
    # bounded, clearly labelled runtime memory context for this turn.
    model_query = task_query + memory_hint + path_instruction
    markdown_requested = _is_markdown_requested(task_query)
    markdown_before = _snapshot_markdown_files(session_dir)
    final_content = None

    try:
        async for chunk in agent.astream({"messages": [{"role": "user", "content": model_query}]}, config=config):
            chunk_final_content = _handle_agent_chunk(chunk)

            if chunk_final_content:
                final_content = chunk_final_content

        # A Graph interruption is not a completed turn. Never run Markdown
        # repair while the graph is paused for SQL approval.
        pending = approval_registry.get(session_id)
        if pending is not None:
            return {
                "status": "waiting_approval",
                "final_content": None,
                "approval": pending.model_dump(mode="json"),
            }

        if markdown_requested and not _has_new_or_updated_markdown(session_dir, markdown_before):
            print("检测到用户明确要求生成 Markdown，" "但本轮未检测到真实 .md 文件，" "开始执行一次自动补救。")

            repair_prompt = f"""
    系统后置校验发现：

    用户原始请求明确要求生成 Markdown 文件，
    但当前工作目录中没有检测到本轮实际
    新生成或更新的 .md 文件。

    请基于本轮已经获取和整理好的全部信息，
    立即调用 generate_markdown 工具
    生成真实的 Markdown 文件。

    要求：

    1. 必须实际调用 generate_markdown 工具；
    2. 不要只在文字中声称“文档已生成”；
    3. 不需要重新搜索已经获得的信息；
    4. Markdown 内容必须满足用户原始请求；
    5. 工作目录仍然是：
       {relative_session_dir_str}

    生成完成后，
    只需要简短确认文档已经生成。
    """

            async for chunk in agent.astream({"messages": [{"role": "user", "content": repair_prompt}]}, config=config):
                chunk_final_content = _handle_agent_chunk(chunk)

                if chunk_final_content and final_content is None:
                    final_content = chunk_final_content

        if markdown_requested and not _has_new_or_updated_markdown(session_dir, markdown_before):
            error = "用户明确要求生成 Markdown，但自动补救后仍未检测到真实 .md 文件。"
            if report_result:
                monitor._emit("error", error)
            return {"status": "failed", "error": error}

        if not isinstance(final_content, str) and final_content is not None:
            final_content = str(final_content)
        if not final_content and markdown_requested and _has_new_or_updated_markdown(session_dir, markdown_before):
            final_content = "Markdown 文档已生成，可在文件列表中下载。"
        if not final_content:
            return {"status": "failed", "error": "Agent 未返回最终回答"}
        if report_result:
            monitor.report_task_result(final_content)
        return {"status": "completed", "final_content": final_content}

    except Exception as e:
        if report_result:
            monitor._emit("error", "执行主智能发生异常信息：" f"{str(e)}")
        return {"status": "failed", "error": str(e)}

    finally:
        reset_session_context(session_dir_token, session_id_token)

# ==========================================================
# HITL Resume
# ==========================================================
async def resume_deep_agent(session_id: str, decision: str, report_result=True):
    """
    根据人工审批结果，
    恢复指定 thread_id 下
    被 interrupt 暂停的 DeepAgent。
    """

    print("恢复 HITL Agent，" f"会话id：{session_id}，" f"人工决策：{decision}")
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir_str = str(session_dir).replace("\\", "/")
    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)

    try:
        agent = await init_main_agent()
        config = {"configurable": {"thread_id": session_id}}
        final_content = None

        async for chunk in agent.astream(Command(resume={"decision": decision}), config=config):
            chunk_final_content = _handle_agent_chunk(chunk)

            if chunk_final_content:
                final_content = chunk_final_content

        pending = approval_registry.get(session_id)
        if pending is not None and pending.status.value == "PENDING":
            return {
                "status": "waiting_approval",
                "approval": pending.model_dump(mode="json"),
            }
        if not isinstance(final_content, str) and final_content is not None:
            final_content = str(final_content)
        if report_result:
            monitor.report_task_result(final_content or "人工审批处理完成。")
        return {
            "status": "completed", "thread_id": session_id,
            "decision": decision,
            "final_content": final_content or "人工审批处理完成。",
        }

    except Exception as e:
        # 修改：
        # 如果 Resume 失败，
        # 将审批状态从 RESUMING 恢复为 PENDING。
        approval_registry.mark_pending(session_id)
        if report_result:
            monitor._emit("error", "恢复 HITL Agent 失败：" f"{str(e)}")
        return {"status": "failed", "error": str(e)}

    finally:
        reset_session_context(session_dir_token, session_id_token)