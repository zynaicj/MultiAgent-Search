"""Deterministic, explicit management for the native AGENTS.md preference section.

The StoreBackend and MemoryMiddleware use the SAME /AGENTS.md Store record.
These helpers deliberately never overwrite unrelated sections or MongoDB data.
"""
import asyncio
import re

from deepagents.backends.utils import create_file_data, file_data_to_string, update_file_data

from agent.memory.native import memory_namespace, STORE_KEY, DEFAULT_USER_ID

SECTION = "## 已保存的长期偏好"
MAX_FILE_CHARS = 16000
_SENSITIVE = re.compile(
    r"password|passcode|secret|api[_ -]?key|access[_ -]?token|身份证|银行卡|密码|密钥",
    re.IGNORECASE,
)
_LOCK = asyncio.Lock()


def _validate(key: str, value: str | None = None) -> tuple[str, str | None]:
    """Stable ASCII keys make deduplication independent of the LLM's phrasing."""
    key = key.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", key):
        raise ValueError("记忆 key 只能使用字母、数字、下划线，以小写英文字母开头，最多 48 字符")
    if _SENSITIVE.search(key):
        raise ValueError("拒绝存储凭据或高敏感信息")
    if value is not None:
        value = value.strip()
        if not value or len(value) > 300 or any(ord(ch) < 32 for ch in value):
            raise ValueError("记忆内容必须为 1～300 字符的单行文本")
        if _SENSITIVE.search(value):
            raise ValueError("拒绝存储凭据或高敏感信息")
    return key, value


def _parse(content: str) -> dict[str, str]:
    """Read only the managed section, preserving other AGENTS.md paragraphs."""
    if SECTION not in content:
        return {}
    managed = content.split(SECTION, 1)[1].split("\n## ", 1)[0]
    found = {}
    for line in managed.splitlines():
        match = re.fullmatch(r"- \*\*([a-z][a-z0-9_]*)\*\*：(.+)", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def _render(content: str, prefs: dict[str, str]) -> str:
    """Rewrite the dedicated section deterministically without disturbing other sections."""
    lines = [SECTION, ""]
    lines.extend(f"- **{key}**：{prefs[key]}" for key in sorted(prefs))
    managed = "\n".join(lines).rstrip() + "\n"
    if SECTION not in content:
        result = content.rstrip() + "\n\n" + managed
    else:
        prefix, suffix = content.split(SECTION, 1)
        next_section = re.search(r"(?m)^## (?!已保存的长期偏好)", suffix)
        tail = suffix[next_section.start():] if next_section else ""
        result = prefix.rstrip() + "\n\n" + managed
        if tail:
            result += "\n" + tail
    if len(result) > MAX_FILE_CHARS:
        raise ValueError("AGENTS.md 超过 16000 字符，请先清理旧记忆")
    return result


async def list_preferences(store, user_id: str = DEFAULT_USER_ID) -> dict[str, str]:
    record = await store.aget(memory_namespace(user_id), STORE_KEY)
    if record is None:
        return {}
    return _parse(file_data_to_string(record.value))


async def set_preference(store, key: str, value: str, user_id: str = DEFAULT_USER_ID) -> str:
    """Add or update by key. Same key+value becomes a no-op."""
    key, value = _validate(key, value)
    async with _LOCK:
        namespace = memory_namespace(user_id)
        current = await store.aget(namespace, STORE_KEY)
        old = file_data_to_string(current.value) if current else "# 用户长期记忆\n"
        prefs = _parse(old)
        if prefs.get(key) == value:
            return "unchanged"
        action = "updated" if key in prefs else "added"
        prefs[key] = value
        updated = _render(old, prefs)
        data = update_file_data(current.value, updated) if current else create_file_data(updated)
        await store.aput(namespace, STORE_KEY, data)
        return action


async def delete_preference(store, key: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Delete exactly one saved key; no-op for missing keys."""
    key, _ = _validate(key)
    async with _LOCK:
        namespace = memory_namespace(user_id)
        current = await store.aget(namespace, STORE_KEY)
        if current is None:
            return False
        old = file_data_to_string(current.value)
        prefs = _parse(old)
        if key not in prefs:
            return False
        del prefs[key]
        await store.aput(namespace, STORE_KEY, update_file_data(current.value, _render(old, prefs)))
        return True
