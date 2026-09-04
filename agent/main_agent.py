from agent.subagents.knowledge_base_agent import knowledge_base_agent
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.network_search_agent import network_search_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# main_agent tool导入
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content

from deepagents import create_deep_agent

from agent.llm import model
from agent.prompts import main_agent_content

from api.monitor import monitor
import asyncio
import uuid
import shutil
from pathlib import Path

from api.context import set_session_context, reset_session_context, set_thread_context

from langchain_core.messages import AIMessage

checkpoint_db_path = Path(__file__).parents[1] / "data" / "checkpoints.db"
checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)

# AsyncSqliteSaver 必须在正在运行的 event loop 中创建，
# 因此不能像原来的 SqliteSaver 一样在模块导入时直接初始化。
_checkpointer_context = None
checkpointer = None
main_agent = None


async def init_main_agent():
    """初始化异步 SQLite Checkpointer 和 Main Agent。"""
    global _checkpointer_context, checkpointer, main_agent

    if main_agent is not None:
        return main_agent

    _checkpointer_context = AsyncSqliteSaver.from_conn_string(
        str(checkpoint_db_path)
    )

    checkpointer = await _checkpointer_context.__aenter__()

    main_agent = create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        tools=[
            generate_markdown,
            convert_md_to_pdf,
            read_file_content,
        ],
        checkpointer=checkpointer,
        subagents=[
            database_query_agent,
            network_search_agent,
            knowledge_base_agent,
        ],
    )

    return main_agent


async def close_main_agent():
    """关闭 SQLite 异步连接。"""
    global _checkpointer_context, checkpointer, main_agent

    if _checkpointer_context is not None:
        await _checkpointer_context.__aexit__(None, None, None)

    _checkpointer_context = None
    checkpointer = None
    main_agent = None

# 执行
"""
  1. 执行主智能体 一定选异步，原因：对应多个客户端
  2. 什么时候触发我们智能体的调用或者执行？？？
  3. 客户端 -》 api/task -> fastapi 接口 -》 异步执行 -》 main_agent的运行 （异步方法）
  4. main_agent执行stream流式处理 -》 调用工具 -》 已经埋好了点  
                                   调用子智能体 -》 结果解析 -》 name = task -> monitor -> 发送子智能体
                                   调用最终结果 -》 结果 -》 monitor -> 发送结果的方法
                                   开启调用以后 -》 当前会话 -》 文件夹地址 -》 推送到前端
"""



project_root_path = Path(__file__).parents[1].resolve() # 绝对 解析路径标识以及软连接
# project_root_path = Path(__file__).parents[1].absolute() # 绝对

def _is_markdown_requested(task_query: str) -> bool:
    """判断用户是否明确要求生成 Markdown 文件。"""
    query = task_query.lower()

    # 明确否定时，不认为用户要求生成 Markdown
    negative_phrases = (
        "不要生成md",
        "不要生成 md",
        "不要生成markdown",
        "不要生成 markdown",
        "无需生成md",
        "不需要生成md",
        "不要生成文件",
    )
    if any(phrase in query for phrase in negative_phrases):
        return False

    markdown_markers = (
        "markdown",
        "md文档",
        "md文件",
        ".md",
    )

    action_markers = (
        "生成",
        "保存",
        "导出",
        "下载",
        "总结成",
        "整理成",
        "写成",
        "转成",
    )

    return (
        any(marker in query for marker in markdown_markers)
        and any(action in query for action in action_markers)
    )


def _snapshot_markdown_files(session_dir: Path) -> dict[str, int]:
    """记录当前 session 已有 Markdown 文件及其修改时间。"""
    snapshot = {}

    for file_path in session_dir.rglob("*.md"):
        if file_path.is_file():
            snapshot[str(file_path.resolve())] = file_path.stat().st_mtime_ns

    return snapshot


def _has_new_or_updated_markdown(
    session_dir: Path,
    before_snapshot: dict[str, int]
) -> bool:
    """检查本轮任务是否真正新建或更新了 Markdown 文件。"""
    for file_path in session_dir.rglob("*.md"):
        if not file_path.is_file():
            continue

        file_key = str(file_path.resolve())
        current_mtime = file_path.stat().st_mtime_ns

        # 新文件
        if file_key not in before_snapshot:
            return True

        # 原文件在本轮被重新写入
        if current_mtime > before_snapshot[file_key]:
            return True

    return False


def _handle_agent_chunk(chunk):
    """处理 Agent 流式状态：监控子 Agent 调用，并提取最终文本。"""
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

                    monitor.report_assistant(
                        args.get("subagent_type", "未知子智能体"),
                        {
                            "description":
                                args.get("description", "")
                        }
                    )

        elif getattr(last_msg, "content", None):
            final_content = last_msg.content

    return final_content





# main_agent.invoke()
# main_agent.stream()
# main_agent.astream() [选他]
async def run_deep_agent(task_query,session_id):
    """
    定义流式+异步执行主智能体！！
    执行过程中，返回  会话文件化返回  调用子智能体  调用最终结果 （monitor）
    task_query: 前端提问的问题
    session_id: 每个前端会话对应的标识 （1.存储session_id ContextVars 2.session_id 给他创建对应的output输出地址）
    """
    print(f"当前会话的main_agent开始执行了！ 会话id:{session_id}")
    # 准备工作 【1. session_dir（前端） 2. relative_session_dir (大模型) 3. 上传的文件拼接上传文件专属提示词】
    # project_root_path / output / session_session_id(uuid)
    # 当前会话存储生成文件的专属文件夹
    session_dir = project_root_path / "output" / f"session_{session_id}"
    # 文件夹可能没有，第一次请求要创建
    session_dir.mkdir(parents=True, exist_ok=True)
    # \  \n \t -> /
    session_dir_str = str(session_dir).replace("\\","/")
    # 获取相对文件夹
    # session_dir : project_root_path / output / session_session_id(uuid)
    # project_root_path : project_root_path
    # relative_session_dir_str: / output / session_session_id(uuid)
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace("\\","/")

    #处理上传文件 （updated / session_session_id）
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = "" # 有上传文件，拼接上传文件专属解析位置的提示词
    if updated_dir_path.exists():
        # 有
        files = [ f.name  for f in updated_dir_path.iterdir()  if f.is_file()]
        # 将上传文件统一赋值到 output_dir 方便前端统一读取 session_dir
        if files:
            for filename in files:
                # 将原文件 -》 复制 -》 目标文件中  （copy2 保留原文件修改时间和权限等元数据）
                shutil.copy2(updated_dir_path / filename, session_dir / filename)
            # 构建提示词！告诉大模型，有上传文件，你要读取上传文件！！
            updated_info_prompt = (f"\n    [已上传文件] 已加载到工作目录:\n" +
                             "\n".join([f"    - {f}" for f in files]) +
                             "\n    请优先使用工具（read_file_content）读取并参考这些文件。")

    # 继续准备 1. 当前会话的对应的session_id session_dir 存储到contextVars [后续工具获取，socket -> 推送消息] 2.调用monitor给前端推送session_dir信息
    session_dir_token = set_session_context(session_dir_str)  # 存储的当前会话对应的文件夹地址
    session_id_token = set_thread_context(session_id)  #获取当前会话的session_id对应socket

    monitor.report_session_dir(session_dir_str)  # 当前会话对应的文件夹地址推送给起前端！

    agent = await init_main_agent()

    # 执行main_agent
    config = {
        "configurable":{
            "thread_id":session_id
        }
    }

    # 构建提示词
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    # 判断用户是否明确要求生成 Markdown
    markdown_requested = _is_markdown_requested(task_query)

    # 记录 Agent 执行前已经存在的 Markdown 文件，
    # 防止把旧文件误认为本轮新生成的文件
    markdown_before = _snapshot_markdown_files(session_dir)

    # 暂存 Main Agent 最后一次文本回答
    final_content = None


    # 反馈结果
    try:
        # ==================== 第一次正常执行 Agent ====================
        async for chunk in agent.astream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": task_query + path_instruction
                    }
                ]
            },
            config=config
        ):
            chunk_final_content = _handle_agent_chunk(chunk)

            if chunk_final_content:
                final_content = chunk_final_content


        # ==================== Markdown 产物后置校验 ====================
        if (
            markdown_requested
            and not _has_new_or_updated_markdown(
                session_dir,
                markdown_before
            )
        ):
            print(
                "检测到用户明确要求生成 Markdown，"
                "但本轮未检测到真实 .md 文件，开始执行一次自动补救。"
            )

            repair_prompt = f"""
    系统后置校验发现：

    用户原始请求明确要求生成 Markdown 文件，
    但当前工作目录中没有检测到本轮实际新生成或更新的 .md 文件。

    请基于本轮已经获取和整理好的全部信息，
    立即调用 generate_markdown 工具生成真实的 Markdown 文件。

    要求：
    1. 必须实际调用 generate_markdown 工具；
    2. 不要只在文字中声称“文档已生成”；
    3. 不需要重新搜索已经获得的信息；
    4. Markdown 内容必须真正满足用户原始请求；
    5. 工作目录仍然是：{relative_session_dir_str}

    生成完成后，只需要简短确认文档已经生成。
    """

            # 使用同一个 thread_id 再执行一轮，
            # 这样 Main Agent 可以继续利用前一轮已有上下文
            async for chunk in agent.astream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": repair_prompt
                        }
                    ]
                },
                config=config
            ):
                chunk_final_content = _handle_agent_chunk(chunk)

                if chunk_final_content and final_content is None:
                    final_content = chunk_final_content 


        # ==================== 最终强校验 ====================
        if (
            markdown_requested
            and not _has_new_or_updated_markdown(
                session_dir,
                markdown_before
            )
        ):
            monitor._emit(
                "error",
                "用户明确要求生成 Markdown，"
                "但自动补救后仍未检测到真实 .md 文件，"
                "本次任务不标记为完成。"
            )
            return


        # ==================== 真正完成任务 ====================
        if final_content:
            print(
                f"主智能体执行结果，最终结果："
                f"{final_content[:100]}"
            )
            monitor.report_task_result(final_content)

        elif markdown_requested and _has_new_or_updated_markdown(
            session_dir,
            markdown_before
        ):
            monitor.report_task_result(
                "Markdown 文档已生成，可在文件列表中下载。"
            )

    except Exception as e:
        monitor._emit(
            "error",
            f"执行主智能发生异常信息：{str(e)}"
        )

    finally:
        reset_session_context(
            session_dir_token,
            session_id_token
        )

