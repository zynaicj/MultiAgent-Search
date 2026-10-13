"""Turn lifecycle for both modes. DeepAgents execution remains in agent/main_agent.py."""
import asyncio
import json

from agent.governance import approval_registry
from agent.governance.models import PendingApproval
from agent.main_agent import run_deep_agent, resume_deep_agent
from agent.collaboration.runner import run_collaboration_agent
from agent.memory.runtime import remember_user_input
from agent.memory.policy import deepagent_hybrid_enabled, MONGO_FACT_TYPES
from api.chat import store
from api.context import set_run_context, reset_run_context, set_thread_context, reset_thread_context
from api.monitor import monitor


async def run_turn(cid: str, rid: str, query: str):
    run_token = set_run_context(rid)
    thread_token = set_thread_context(cid)
    try:
        conversation = store.get_conversation(cid)
        if not conversation:
            raise RuntimeError("会话已不存在")
        if conversation["mode"] == "collaboration":
            previous = [m for m in store.list_messages(cid) if m.get("run_id") != rid][-6:]
            history_context = "\n".join(
                ("用户" if m["role"] == "user" else "Agent") + "：" + m["content"][:800]
                for m in previous
            )
            graph_result = await run_collaboration_agent(
                query, cid, report_result=False, history_context=history_context
            )
            if not graph_result.get("final_answer"):
                result = {"status": "failed", "error": "协作工作流未生成最终回答"}
            else:
                result = {"status": "completed", "final_content": graph_result["final_answer"]}
        else:
            result = await run_deep_agent(query, cid, report_result=False)
        status = result.get("status", "failed")
        if status == "waiting_approval":
            approval = result.get("approval")
            store.finish_run(rid, "waiting_approval", approval=approval)
            monitor._emit("run_status", "等待人工审批", {"status": "waiting_approval", "run_id": rid, "approval": approval})
            return
        if status != "completed":
            error = result.get("error", "Agent 未返回有效答案")
            store.finish_run(rid, "failed", error=error)
            monitor._emit("error", error, {"run_id": rid})
            return
        answer = result.get("final_content") or "任务已完成，但未返回文本结果。"
        store.finish_run(rid, "completed", answer=answer)
        monitor.report_task_result(answer)
        if conversation["mode"] == "deep_agent" and deepagent_hybrid_enabled():
            await remember_user_input(query, allowed_types=MONGO_FACT_TYPES)
    except Exception as exc:
        store.finish_run(rid, "failed", error=str(exc))
        monitor._emit("error", f"执行失败：{exc}", {"run_id": rid})
    finally:
        reset_run_context(run_token)
        reset_thread_context(thread_token)


async def resume_turn(cid: str, rid: str, decision: str):
    run_token = set_run_context(rid)
    thread_token = set_thread_context(cid)
    try:
        result = await resume_deep_agent(cid, decision, report_result=False)
        status = result.get("status", "failed")
        if status == "waiting_approval":
            approval = result.get("approval")
            store.finish_run(rid, "waiting_approval", approval=approval)
            monitor._emit("run_status", "等待下一次人工审批", {"status": "waiting_approval", "run_id": rid, "approval": approval})
        elif status == "completed":
            answer = result.get("final_content") or "已处理人工审批决定。"
            store.finish_run(rid, "completed", answer=answer)
            monitor.report_task_result(answer)
            original = store.get_run(rid)
            if original and deepagent_hybrid_enabled():
                await remember_user_input(original["query"], allowed_types=MONGO_FACT_TYPES)
        else:
            error = result.get("error", "审批恢复失败")
            store.finish_run(rid, "waiting_approval", approval=store.get_run(rid) and json.loads(store.get_run(rid).get("approval_json") or "null"))
            approval_registry.mark_pending(cid)
            monitor._emit("error", error, {"run_id": rid})
    except Exception as exc:
        original = store.get_run(rid) or {}
        approval = json.loads(original.get("approval_json") or "null")
        store.finish_run(rid, "waiting_approval", approval=approval)
        approval_registry.mark_pending(cid)
        monitor._emit("error", f"审批恢复失败：{exc}", {"run_id": rid})
    finally:
        reset_run_context(run_token)
        reset_thread_context(thread_token)


def recover_runs():
    """Restart recovery for paused HITL requests; in-flight runs are marked failed."""
    pending = store.restore_interrupted_runs()
    for run in pending:
        try:
            payload = json.loads(run.get("approval_json") or "null")
            if payload:
                approval_registry.register(PendingApproval(**payload))
        except Exception as exc:
            print(f"[Chat] 审批状态恢复失败 ({run['id']}): {exc}")


def create_background(coro):
    """Keep exception accounting in run_turn/resume_turn; no awaited HTTP request."""
    return asyncio.create_task(coro)
