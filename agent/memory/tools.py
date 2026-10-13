"""Tools for explicit, verifiable DeepAgents long-term preference management.

Agent may call these only when the user asks to remember, update, forget, or
inspect a non-sensitive preference. No implicit extraction from every message.
"""
from langchain_core.tools import tool

from agent.memory.native import init_native_store
from agent.memory.manage import delete_preference, list_preferences, set_preference


@tool
async def save_long_term_preference(key: str, value: str) -> str:
    """当用户明确要求长期记住或修改一个稳定、非敏感偏好时调用。

    key: 稳定的英文 snake_case 标识，如 python_explanation_style。
    value: 单行中文偏好。相同 key 将更新旧值，重复写入相同值不创建副本。
    禁止用于保存密码、令牌、密钥或敏感个人信息。
    """
    try:
        store = await init_native_store()
        action = await set_preference(store, key, value)
        stored = (await list_preferences(store)).get(key.strip().lower())
        if stored != value.strip():
            return "保存后校验失败，不能向用户声称已记住"
        return f"持久化校验成功：{action}，key={key.strip().lower()}，value={stored}"
    except ValueError as exc:
        return f"长期记忆写入被拒绝：{exc}"


@tool
async def forget_long_term_preference(key: str) -> str:
    """当用户明确要求忘记一项已保存的长期偏好时，按其稳定 key 删除。"""
    try:
        store = await init_native_store()
        deleted = await delete_preference(store, key)
        remaining = await list_preferences(store)
        if key.strip().lower() in remaining:
            return "删除后校验失败，不能向用户声称已删除"
        return "指定偏好已从持久化存储删除" if deleted else "未找到该偏好，无需删除"
    except ValueError as exc:
        return f"长期记忆删除被拒绝：{exc}"


@tool
async def list_long_term_preferences() -> str:
    """查看当前本地用户的已保存偏好，供明确要求列出记忆时使用。"""
    prefs = await list_preferences(await init_native_store())
    if not prefs:
        return "没有通过偏好管理工具保存的长期偏好"
    return "\n".join(f"- {key}: {value}" for key, value in sorted(prefs.items()))
