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

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
project_root_str = str(project_root)

if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)


# ==========================================================
# Windows UTF-8
# ==========================================================

os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ==========================================================
# Third-Party Imports
# ==========================================================

import uvicorn
from typing import List, Literal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles as FastAPIStaticFiles
from pydantic import BaseModel


# ==========================================================
# Project Imports
#
# 注意：
# 到这里时 project_root
# 已经加入 sys.path。
# ==========================================================

from agent.main_agent import run_deep_agent, init_main_agent, close_main_agent, resume_deep_agent
from agent.collaboration.runner import run_collaboration_agent
from agent.governance import approval_registry
from api.monitor import manager
from api.chat import store, service


# ==========================================================
# FastAPI App
# ==========================================================

app = FastAPI(title="DeepAgents API")


# ==========================================================
# Directories
# ==========================================================

output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)
static_dir = current_dir / "static"
static_dir.mkdir(exist_ok=True)

app.mount("/static", FastAPIStaticFiles(directory=str(static_dir)), name="static")
app.mount("/output", FastAPIStaticFiles(directory=str(output_dir)), name="output")


# ==========================================================
# 首页
# ==========================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "<h1>MultiAgent-Search API</h1><p>访问 <a href='/docs'>/docs</a> 查看 API 文档</p>"


# ==========================================================
# CORS
# ==========================================================

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
    )


# ==========================================================
# Request Models
# ==========================================================

class TaskRequest(BaseModel):
    query: str
    thread_id: str | None = None
    mode: Literal["deep_agent", "collaboration"] = "deep_agent"


class ApprovalRequest(BaseModel):
    """
    Human-in-the-Loop
    人工审批请求。
    """

    thread_id: str
    decision: Literal["approve", "reject"]


# ==========================================================
# Startup
# ==========================================================

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    await init_main_agent()
    service.recover_runs()
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")



# ==========================================================
# Durable conversation APIs
# ==========================================================

class CreateChatRequest(BaseModel):
    title: str = "新聊天"
    mode: Literal["deep_agent", "collaboration"] = "deep_agent"


class ChatMessageRequest(BaseModel):
    query: str
    client_message_id: str


class RenameChatRequest(BaseModel):
    title: str


class ChatApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]


@app.get("/api/conversations")
async def get_conversations():
    return {"conversations": store.list_conversations()}


@app.post("/api/conversations")
async def create_conversation(body: CreateChatRequest):
    return store.create_conversation(title=body.title, mode=body.mode)


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    result = store.get_conversation(cid)
    if not result:
        raise HTTPException(404, "会话不存在")
    return result


@app.patch("/api/conversations/{cid}")
async def rename_conversation(cid: str, body: RenameChatRequest):
    if not body.title.strip():
        raise HTTPException(422, "标题不能为空")
    if not store.rename_conversation(cid, body.title):
        raise HTTPException(404, "会话不存在")
    return store.get_conversation(cid)


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    # Refuse to delete while the Agent is working or waiting for approval.
    conversation = store.get_conversation(cid)
    if not conversation:
        raise HTTPException(404, "会话不存在")
    if conversation["status"] in {"running", "waiting_approval"}:
        raise HTTPException(409, "请先完成当前任务或审批")
    # Clear LangGraph memory before removing chat metadata; avoid a ghost
    # checkpoint being accidentally reused for a newly created conversation.
    from agent.main_agent import checkpointer
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        try:
            await checkpointer.adelete_thread(cid)
        except Exception as exc:
            raise HTTPException(500, f"删除 Checkpoint 失败：{exc}")
    result = store.delete_conversation(cid)
    if result == "busy":
        raise HTTPException(409, "当前会话正在执行")
    shutil.rmtree(output_dir / f"session_{cid}", ignore_errors=True)
    shutil.rmtree(updated_dir / f"session_{cid}", ignore_errors=True)
    return {"status": "deleted"}


@app.get("/api/conversations/{cid}/messages")
async def get_messages(cid: str):
    if not store.get_conversation(cid):
        raise HTTPException(404, "会话不存在")
    return {"messages": store.list_messages(cid)}


@app.get("/api/conversations/{cid}/events")
async def get_events(cid: str, after: int = 0, limit: int = 300):
    if not store.get_conversation(cid):
        raise HTTPException(404, "会话不存在")
    return {"events": store.list_events(cid, after=after, limit=limit)}


@app.get("/api/conversations/{cid}/files")
async def get_chat_files(cid: str):
    if not store.get_conversation(cid):
        raise HTTPException(404, "会话不存在")
    folder = output_dir / f"session_{cid}"
    if not folder.exists():
        return {"files": []}
    files = []
    for path in folder.rglob("*"):
        if path.is_file():
            files.append({"name": str(path.relative_to(folder)).replace("\\", "/"),
                          "size": path.stat().st_size})
    return {"files": files}


@app.get("/api/conversations/{cid}/files/{filename:path}")
async def download_chat_file(cid: str, filename: str):
    if not store.get_conversation(cid):
        raise HTTPException(404, "会话不存在")
    base = (output_dir / f"session_{cid}").resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=path.name)


@app.get("/api/runs/{rid}")
async def get_run(rid: str):
    result = store.get_run(rid)
    if not result:
        raise HTTPException(404, "运行不存在")
    return result


@app.post("/api/conversations/{cid}/messages", status_code=202)
async def send_chat_message(cid: str, body: ChatMessageRequest):
    query = body.query.strip()
    if not query or not body.client_message_id.strip():
        raise HTTPException(422, "消息不能为空")
    if len(query) > 20000:
        raise HTTPException(422, "消息过长")
    status, run = store.begin_run(cid, query, body.client_message_id)
    if status == "missing":
        raise HTTPException(404, "会话不存在")
    if status == "busy":
        raise HTTPException(409, "该会话仍在执行或等待审批")
    if status == "started":
        service.create_background(service.run_turn(cid, run["id"], query))
    return {"status": status if status == "duplicate" else "running",
            "conversation_id": cid, "run_id": run["id"]}


@app.post("/api/runs/{rid}/approval", status_code=202)
async def approve_chat_run(rid: str, body: ChatApprovalRequest):
    run = store.get_run(rid)
    if not run:
        raise HTTPException(404, "运行不存在")
    cid = run["conversation_id"]
    pending = approval_registry.get(cid)
    if not pending:
        raise HTTPException(409, "当前会话没有可恢复的人工审批")
    if body.decision not in pending.allowed_decisions:
        raise HTTPException(400, "不支持的审批决定")
    if store.begin_resume(cid, rid) != "started":
        raise HTTPException(409, "当前运行不在等待审批状态")
    if not approval_registry.mark_resuming(cid):
        store.finish_run(rid, "waiting_approval", approval=pending.model_dump(mode="json"))
        raise HTTPException(409, "审批正在恢复中")
    service.create_background(service.resume_turn(cid, rid, body.decision))
    return {"status": "resuming", "conversation_id": cid, "run_id": rid}

# ==========================================================
# 启动 Agent Task
# ==========================================================

@app.post("/api/task")
async def run_task(request: TaskRequest):
    thread_id = request.thread_id or str(uuid.uuid4())

    if request.mode == "collaboration":
        asyncio.create_task(run_collaboration_agent(request.query, thread_id))
    elif request.mode == "deep_agent":
        asyncio.create_task(run_deep_agent(request.query, thread_id))

    return {"status": "started", "thread_id": thread_id, "mode": request.mode}


# ==========================================================
# Human-in-the-Loop Approval API
# ==========================================================

@app.post("/api/approval")
async def approve_tool(request: ApprovalRequest):
    """
    接收人工审批结果，
    恢复对应 thread_id 下
    被 interrupt 暂停的 DeepAgent。
    """

    pending = approval_registry.get(request.thread_id)

    if pending is None:
        raise HTTPException(status_code=404, detail="当前会话没有等待处理的审批任务")

    if request.decision not in pending.allowed_decisions:
        raise HTTPException(status_code=400, detail="当前审批不支持该 decision")

    resuming = approval_registry.mark_resuming(request.thread_id)

    if resuming is None:
        raise HTTPException(status_code=409, detail="当前审批正在处理中，请勿重复提交")

    asyncio.create_task(resume_deep_agent(session_id=request.thread_id, decision=request.decision))

    return {"status": "resuming", "thread_id": request.thread_id, "decision": request.decision, "tool_name": pending.tool_name, "operation": pending.operation}


# ==========================================================
# Shutdown
# ==========================================================

@app.on_event("shutdown")
async def shutdown_event():
    await close_main_agent()


# ==========================================================
# Upload
# ==========================================================

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    保存上传文件到：

    updated/session_{thread_id}
    """

    import re
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", thread_id):
        raise HTTPException(422, "不合法的会话 ID")
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []

    for file in files:
        if not file.filename or Path(file.filename).name != file.filename or file.filename in {".", ".."}:
            raise HTTPException(422, "不合法的文件名")
        file_path = target_dir / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


# ==========================================================
# Download
# ==========================================================

@app.get("/api/download")
async def download_file(path: str):
    try:
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}

    if not abs_path.exists():
        return {"error": "文件不存在"}

    return FileResponse(abs_path, filename=abs_path.name)


# ==========================================================
# File List
# ==========================================================

@app.get("/api/files")
async def list_files(path: str | None = None):
    if path is None:
        path = str(output_dir)

    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}
    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []

    try:
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append({"name": file_path.name, "type": "file", "path": str(file_path), "size": stat.st_size, "mtime": stat.st_mtime})
    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


# ==========================================================
# WebSocket
# ==========================================================

@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    print(f"会话向我们发起了请求，要求建立连接：{thread_id} 对应：{websocket}")

    await manager.connect(websocket, thread_id)

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong", "message": f"服务端已收到: {data}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")
    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)


# ==========================================================
# Local Start
# ==========================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)