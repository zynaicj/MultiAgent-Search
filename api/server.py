import os
import sys
import uuid
import asyncio
import shutil

from pathlib import Path


# ==========================================================
# 修改：
# 必须在导入 api.xxx / agent.xxx 之前
# 把项目根目录加入 Python 模块搜索路径。
#
# 这样使用：
#
# python api/server.py
#
# 启动时，也可以正常识别：
#
# api.monitor
# agent.main_agent
# agent.governance
# ==========================================================

current_dir = (
    Path(__file__)
    .resolve()
    .parent
)

project_root = (
    current_dir.parent
)

project_root_str = str(
    project_root
)

if (
    project_root_str
    not in sys.path
):

    sys.path.insert(
        0,
        project_root_str,
    )


# ==========================================================
# Windows UTF-8
# ==========================================================

os.environ[
    "PYTHONIOENCODING"
] = "utf-8"


if hasattr(
    sys.stdout,
    "reconfigure",
):
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="replace",
    )


if hasattr(
    sys.stderr,
    "reconfigure",
):
    sys.stderr.reconfigure(
        encoding="utf-8",
        errors="replace",
    )


# ==========================================================
# Third-Party Imports
# ==========================================================

import uvicorn


from typing import (
    List,
    Literal,
)


from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    Form,
    HTTPException,
)


from fastapi.responses import (
    FileResponse,
    HTMLResponse,
)


from fastapi.middleware.cors import (
    CORSMiddleware,
)


from fastapi.staticfiles import (
    StaticFiles as FastAPIStaticFiles,
)


from pydantic import (
    BaseModel,
)


# ==========================================================
# Project Imports
#
# 注意：
# 到这里时 project_root
# 已经加入 sys.path。
# ==========================================================

from agent.main_agent import (
    run_deep_agent,
    init_main_agent,
    close_main_agent,
    resume_deep_agent,
)


from agent.collaboration.runner import (
    run_collaboration_agent,
)


from agent.governance import (
    approval_registry,
)


from api.monitor import (
    manager,
)


# ==========================================================
# FastAPI App
# ==========================================================

app = FastAPI(
    title="DeepAgents API"
)


# ==========================================================
# Directories
# ==========================================================

output_dir = (
    project_root
    / "output"
)

output_dir.mkdir(
    exist_ok=True
)


updated_dir = (
    project_root
    / "updated"
)

updated_dir.mkdir(
    exist_ok=True
)


static_dir = (
    current_dir
    / "static"
)

static_dir.mkdir(
    exist_ok=True
)


app.mount(
    "/static",

    FastAPIStaticFiles(
        directory=str(
            static_dir
        )
    ),

    name="static",
)


app.mount(
    "/output",

    FastAPIStaticFiles(
        directory=str(
            output_dir
        )
    ),

    name="output",
)


# ==========================================================
# 首页
# ==========================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def root():

    index_path = (
        static_dir
        / "index.html"
    )


    if index_path.exists():

        return (
            index_path.read_text(
                encoding="utf-8"
            )
        )


    return (
        "<h1>MultiAgent-Search API</h1>"
        "<p>访问 "
        "<a href='/docs'>/docs</a> "
        "查看 API 文档</p>"
    )


# ==========================================================
# CORS
# ==========================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "*"
    ],

    allow_credentials=True,

    allow_methods=[
        "*"
    ],

    allow_headers=[
        "*"
    ],
)


# ==========================================================
# Request Models
# ==========================================================

class TaskRequest(
    BaseModel
):

    query: str

    thread_id: (
        str | None
    ) = None

    mode: Literal[
        "deep_agent",
        "collaboration",
    ] = "deep_agent"


class ApprovalRequest(
    BaseModel
):
    """
    Human-in-the-Loop
    人工审批请求。
    """

    thread_id: str

    decision: Literal[
        "approve",
        "reject",
    ]


# ==========================================================
# Startup
# ==========================================================

@app.on_event(
    "startup"
)
async def startup_event():

    loop = (
        asyncio
        .get_running_loop()
    )


    manager.set_loop(
        loop
    )


    # 初始化 DeepAgents Checkpointer
    await init_main_agent()


    print(
        "[Server] WebSocket Manager "
        f"bound to loop: {id(loop)}"
    )


# ==========================================================
# 启动 Agent Task
# ==========================================================

@app.post(
    "/api/task"
)
async def run_task(
    request: TaskRequest
):

    thread_id = (
        request.thread_id
        or str(
            uuid.uuid4()
        )
    )


    # ======================================================
    # Multi-Agent Collaboration
    # ======================================================

    if (
        request.mode
        == "collaboration"
    ):

        asyncio.create_task(
            run_collaboration_agent(
                request.query,
                thread_id,
            )
        )


    # ======================================================
    # 原 DeepAgents 模式
    # ======================================================

    elif (
        request.mode
        == "deep_agent"
    ):

        asyncio.create_task(
            run_deep_agent(
                request.query,
                thread_id,
            )
        )


    return {
        "status":
            "started",

        "thread_id":
            thread_id,

        "mode":
            request.mode,
    }


# ==========================================================
# Human-in-the-Loop Approval API
# ==========================================================

@app.post(
    "/api/approval"
)
async def approve_tool(
    request: ApprovalRequest
):
    """
    接收人工审批结果，
    恢复对应 thread_id 下
    被 interrupt 暂停的 DeepAgent。
    """


    # ======================================================
    # 1. 当前 thread
    # 必须真的存在待审批任务
    # ======================================================

    pending = (
        approval_registry.get(
            request.thread_id
        )
    )


    if pending is None:

        raise HTTPException(
            status_code=404,

            detail=(
                "当前会话没有等待处理的"
                "审批任务"
            ),
        )


    # ======================================================
    # 2. decision 必须合法
    # ======================================================

    if (
        request.decision
        not in
        pending.allowed_decisions
    ):

        raise HTTPException(
            status_code=400,

            detail=(
                "当前审批不支持"
                "该 decision"
            ),
        )


    # ======================================================
    # 3. PENDING → RESUMING
    #
    # 防止重复点击导致同一个
    # Graph 被 Resume 两次。
    # ======================================================

    resuming = (
        approval_registry
        .mark_resuming(
            request.thread_id
        )
    )


    if resuming is None:

        raise HTTPException(
            status_code=409,

            detail=(
                "当前审批正在处理中，"
                "请勿重复提交"
            ),
        )


    # ======================================================
    # 4. 真正恢复 Agent
    # ======================================================

    asyncio.create_task(
        resume_deep_agent(
            session_id=(
                request.thread_id
            ),

            decision=(
                request.decision
            ),
        )
    )


    return {
        "status":
            "resuming",

        "thread_id":
            request.thread_id,

        "decision":
            request.decision,

        "tool_name":
            pending.tool_name,

        "operation":
            pending.operation,
    }


# ==========================================================
# Shutdown
# ==========================================================

@app.on_event(
    "shutdown"
)
async def shutdown_event():

    await close_main_agent()


# ==========================================================
# Upload
# ==========================================================

@app.post(
    "/api/upload"
)
async def upload_files(
    files: List[
        UploadFile
    ] = File(...),

    thread_id: str = Form(...),
):
    """
    保存上传文件到：

    updated/session_{thread_id}
    """

    target_dir = (
        updated_dir
        / f"session_{thread_id}"
    )


    target_dir.mkdir(
        parents=True,
        exist_ok=True,
    )


    saved_files = []


    for file in files:

        file_path = (
            target_dir
            / file.filename
        )


        with file_path.open(
            "wb"
        ) as buffer:

            shutil.copyfileobj(
                file.file,
                buffer,
            )


        saved_files.append(
            file.filename
        )


    return {
        "status":
            "uploaded",

        "files":
            saved_files,
    }


# ==========================================================
# Download
# ==========================================================

@app.get(
    "/api/download"
)
async def download_file(
    path: str
):

    try:

        abs_path = (
            Path(path)
            .resolve()
        )


        output_abs = (
            output_dir
            .resolve()
        )


        if not (
            abs_path
            .is_relative_to(
                output_abs
            )
        ):

            return {
                "error":
                    "拒绝访问: "
                    "只能下载输出目录下的文件"
            }


    except Exception:

        return {
            "error":
                "无效的路径参数"
        }


    if not (
        abs_path.exists()
    ):

        return {
            "error":
                "文件不存在"
        }


    return FileResponse(
        abs_path,

        filename=(
            abs_path.name
        ),
    )


# ==========================================================
# File List
# ==========================================================

@app.get(
    "/api/files"
)
async def list_files(
    path: str | None = None
):

    if path is None:

        path = str(
            output_dir
        )


    print(
        f"[DEBUG] 请求文件列表: "
        f"{path}"
    )


    try:

        abs_path = (
            Path(path)
            .resolve()
        )


        output_abs = (
            output_dir
            .resolve()
        )


        if not (
            abs_path
            .is_relative_to(
                output_abs
            )
        ):

            print(
                "[ERROR] 拒绝访问: "
                f"{abs_path} "
                f"不在 {output_abs} 目录下"
            )


            return {
                "error":
                    "拒绝访问: "
                    "只能访问输出目录下的文件"
            }


    except Exception as e:

        print(
            "[ERROR] 路径解析失败: "
            f"{e}"
        )


        return {
            "error":
                f"路径无效: {e}"
        }


    if not (
        abs_path.exists()
    ):

        return {
            "error":
                "目录不存在"
        }


    files = []


    try:

        for file_path in (
            abs_path.rglob(
                "*"
            )
        ):

            if (
                file_path.is_file()
            ):

                stat = (
                    file_path.stat()
                )


                files.append(
                    {
                        "name":
                            file_path.name,

                        "type":
                            "file",

                        "path":
                            str(
                                file_path
                            ),

                        "size":
                            stat.st_size,

                        "mtime":
                            stat.st_mtime,
                    }
                )


    except Exception as e:

        print(
            "[ERROR] 遍历文件失败: "
            f"{e}"
        )


        return {
            "error":
                str(e)
        }


    files.sort(
        key=lambda x:
            x.get(
                "mtime",
                0,
            ),

        reverse=True,
    )


    print(
        f"[DEBUG] 找到 "
        f"{len(files)} 个文件"
    )


    return {
        "files":
            files
    }


# ==========================================================
# WebSocket
# ==========================================================

@app.websocket(
    "/ws/{thread_id}"
)
async def websocket_endpoint(
    websocket: WebSocket,
    thread_id: str,
):

    print(
        "会话向我们发起了请求，"
        f"要求建立连接："
        f"{thread_id} "
        f"对应：{websocket}"
    )


    # 建立并注册 WebSocket
    await manager.connect(
        websocket,
        thread_id,
    )


    try:

        while True:

            # 保持 WebSocket 长连接
            data = (
                await websocket
                .receive_text()
            )


            await websocket.send_json(
                {
                    "type":
                        "pong",

                    "message":
                        (
                            "服务端已收到: "
                            f"{data}"
                        ),
                }
            )


    except WebSocketDisconnect:

        manager.disconnect(
            websocket,
            thread_id,
        )


        print(
            "[WebSocket] "
            "客户端已断开: "
            f"{thread_id}"
        )


    except Exception as e:

        print(
            "[WebSocket] "
            "连接异常: "
            f"{e}"
        )


        manager.disconnect(
            websocket,
            thread_id,
        )


# ==========================================================
# Local Start
# ==========================================================

if __name__ == "__main__":

    uvicorn.run(
        app,

        host="0.0.0.0",

        port=8000,

        reload=False,
    )