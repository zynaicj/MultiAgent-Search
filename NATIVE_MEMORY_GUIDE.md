# MultiAgent-Search：DeepAgents 原生长期记忆（独立开发分支）

**分支**：`feature/deepagents-native-memory`  
**基线**：`feature/deepagents-chat-integrated`  
**DeepAgents 版本**：保持 `0.4.3`（经对照该版本源代码，已支持 `backend` / `memory` / `store`，暂不升级）

## 1. 原理和三个数据库

```mermaid
flowchart TD
    U["用户提问"] --> A["DeepAgents Main Agent"]
    A <--> CP[("data/checkpoints.db\n单会话短期记忆")]
    A --> MM["MemoryMiddleware\n加载 /memories/AGENTS.md"]
    MM <--> CB["CompositeBackend"]
    CB -->|"/memories/ 路由"| SB["StoreBackend"]
    SB <--> ST[("data/native_memory.db\n跨会话长期记忆")]
    CB -->|"其他内置虚拟文件"| STATE["StateBackend\n会话状态"]
    A --> M["旧 MongoDB Memory（DeepAgent 默认关闭，可选启用）"]
```

- **Checkpoint（短期）**：按 `thread_id` 保存运行状态，仅同一聊天线程复用。
- **LangGraph Store（长期）**：通过 `("deepagents_memory", user_id)` 命名空间保存与聊天线程无关的持久化虚拟文件。
- **MongoDB（既有独立 Memory）**：原有 Extractor/Manager/Recall 代码、数据和 Collaboration 模式保持不动。

`/memories/AGENTS.md` 是 DeepAgents 能看到的**虚拟文件路径**。经过 `CompositeBackend` 后，它实际对应 Store 中的键 `/AGENTS.md`，并不是项目根目录下的真实 Markdown 文件。

## 2. 实际新增和改动

| 文件 | 改动 |
|---|---|
| `agent/memory/native.py` | 新增持久化 Store 生命周期、命名空间、Backend 工厂、记忆文件初始化 |
| `agent/memory/manage.py` | 对偏好按 key 增加/更新、去重、删除，尽量不改动其他段落 |
| `agent/memory/tools.py` | 三个可供模型主动调用的保存/删除/查看工具，写后校验 |
| `agent/memory/refresh.py` | 每轮从 Store 重新加载 AGENTS.md，避免旧 thread 缓存过时 |
| `agent/main_agent.py` | 配置 `backend`、`memory`、`store`；区分虚拟记忆文件与报告输出 |
| `api/chat/service.py` | 默认不再为 DeepAgents 调用旧 MongoDB Extractor（防止重复） |
| `tests/test_native_memory.py` | 记忆文件保存、重新连接、命名空间隔离 |
| `tests/test_native_memory_agent.py` | 无网络假模型验证实际 `create_deep_agent()` 加载 AGENTS.md |
| `tests/test_native_memory_management.py` | 相同 key 去重/更新/删除、真实工具调用、同 thread 连续追问刷新 |
| `.github/workflows/native-memory-check.yml` | 语法、存储、Agent Middleware、聊天数据库回归测试 |

旧分支未被覆盖。

## 3. 三个关键配置

在 `agent/main_agent.py` 的 `init_main_agent()` 中：

```python
memory_store = await init_native_store()

main_agent = create_deep_agent(
    # 现有的 model、tools、subagents 原样保留
    checkpointer=checkpointer,
    store=memory_store,               # 真正持久化到 SQLite 的 BaseStore
    backend=backend_factory_for(),   # /memories/ 走 StoreBackend
    memory=["/memories/AGENTS.md"],  # MemoryMiddleware 加载这个文件
)
```

**仅添加 `memory=[...]` 不会自动保证持久化。** 必须提供正确的 Backend 和持久化 Store；只用 `InMemoryStore` 无法跨服务重启持久保存。

这套代码对 `deepagents==0.4.3` 使用 `lambda runtime` 风格的 Backend 工厂，不直接套新版 API 的零参数写法。

## 4. 本地同步与启动（PowerShell）

请先保存或提交本地未提交修改，再切换远端分支：

```powershell
git status
git fetch origin
git switch --track origin/feature/deepagents-native-memory
python -m compileall -q agent api tools
python -m unittest discover -s tests -p "test_native_memory.py" -v
python -m unittest discover -s tests -p "test_native_memory_agent.py" -v
python -m unittest discover -s tests -p "test_native_memory_management.py" -v
python api/server.py
```

浏览器访问 http://127.0.0.1:8000 。

如果本地已有同名分支，可用 `git switch feature/deepagents-native-memory` 与 `git pull --ff-only`；不要使用 `git reset --hard`。

## 5. 实际功能验证（不调用 MongoDB）

确保 `.env` 没有启用 `DEEPAGENT_MONGO_MEMORY_ENABLED=1`。本次新分支默认使用原生 Memory；旧 MongoDB 记忆仍可用于协作模式。

- **新聊天 A（DeepAgents 智能模式）**：发送“请把这个长期偏好写入 `/memories/AGENTS.md`：以后解释 Python 代码要给详细中文注释。请实际调用 read_file / edit_file 更新，不要只在文字中说已经记住。”
- 查看右侧日志，确认有 `read_file` / `edit_file` 工具调用。**如果没有实际文件工具调用，不能视为写入成功**。
- **新聊天 B（DeepAgents 智能模式）**：发送“我之前让你记住了怎样的 Python 讲解偏好？”。注意，这里是全新的 `conversation_id` 和 `thread_id`。
- 关闭服务、重新运行 `python api/server.py`，再创建新聊天 C 并提问相同问题，验证 SQLite 文件的持久化。

这里先检验原生 Memory，避免此前“蓝鲸27”测试中长期 MongoDB 和短期 Checkpoint 共同影响结果的混淆。

## 6. 手动检查 AGENTS.md（独立临时文件）

如需要确认数据库内容，可以暂时停止服务，并在项目根目录创建/复用唯一的 `temp_test.py`：

```python
import asyncio
from agent.memory.native import (
    init_native_store,
    read_native_memory,
    close_native_store,
)

async def main():
    try:
        store = await init_native_store()
        content = await read_native_memory(store)
        print(content)
    finally:
        await close_native_store()

asyncio.run(main())
```

运行 `python temp_test.py` 即可；测试结束覆盖或删除此文件。

## 7. 配置、兼容性和安全边界

- `NATIVE_MEMORY_USER_ID=local_user` 为**本地单用户环境**的默认命名空间；若之后做多租户，必须在通过身份认证之后传入受信任的 user_id，而不是相信前端任意传来的 ID。测试已覆盖不同命名空间隔离，不代表整个 Web 产品已有身份认证。
- `DEEPAGENT_MONGO_MEMORY_ENABLED=0`（默认）让 DeepAgents 原生长期记忆与旧 MongoDB Recall 分离；设为 `1` 可打开旧 MongoDB 召回和提取，但应先设计冲突处理规则。Collaboration 模式仍保持原有 MongoDB Memory。
- `data/native_memory.db` 要持久保存并备份；服务停止时关闭连接，但不会删除文件。
- AGENTS.md 是**默认加载的长期上下文**，不适合无限追加大量项目事实；只保存少量明确、稳定且不敏感的偏好、事实。后续大量事实仍适合结构化 MongoDB 检索。
- `MemoryMiddleware` 不等于自动提取器；**模型必须实际调用 save_long_term_preference 或文件工具才会保存新增内容**。不要把“AI 说记住了”当成写入证明。
- DeepAgents 0.4.3 原生 MemoryMiddleware 在状态已有 `memory_contents` 时会跳过加载。本分支新增 `RefreshNativeMemoryMiddleware`，每轮从 SQLite Store 读取最新 AGENTS.md 覆盖缓存；已通过同 thread 的增改删自动化测试。
- 整套应用当前没有完善的身份认证和细粒度权限控制，Web 服务不应直接暴露公网使用；尤其要谨慎赋予模型文件和数据库写操作权限。
- GitHub Actions 验证不消耗模型额度；**真实 Qwen 调用及真实模型自主选择记忆工具仍需本地手动验收**。

## 8. 面试可用的实现说明

> 项目的短期记忆由 LangGraph Checkpointer + thread_id 实现；长期记忆使用 DeepAgents 自带的 MemoryMiddleware，从 /memories/AGENTS.md 加载稳定的用户偏好。通过 CompositeBackend 把记忆路径路由到 StoreBackend，底层使用持久化 SQLite Store；Store 的命名空间不依赖 thread_id，因此不同聊天可以复用同一用户长期记忆。原有 MongoDB 结构化记忆仍保留，专门面向数量多、需筛选召回的项目事实。这两层存储职责不同，并做了隔离，避免重复注入。


## 9. 第二阶段：可验证的长期偏好管理

本阶段已新增三个给 Main Agent 调用的工具：

| 工具名 | 意义 | 数据库结果 |
|---|---|---|
| `save_long_term_preference(key, value)` | 保存或更新一条用户明确指定的长期偏好 | 同 key 内容相同不重复，内容变化则替换旧值，写后回读 |
| `forget_long_term_preference(key)` | 删除一个指定偏好 | 删后回读校验 |
| `list_long_term_preferences()` | 列出通过管理工具保存的偏好 | 实际读取 SQLite |

示例：在聊天 A 中说“以后讲 Python 代码请给详细中文注释，记住这条偏好”，Agent 应调用 `save_long_term_preference`，键可用 `python_explanation_style`。

在同一聊天 A 继续说“把刚才的偏好改成简洁示例”，应当使用**相同的 key** 调用 `save_long_term_preference`，不会新增第二条重复偏好。重新询问偏好时，`RefreshNativeMemoryMiddleware` 会加载最新持久化内容。

再新建聊天 B，问“你记得我偏好的 Python 讲解风格吗？”以检验跨 thread 召回。也可发送“请调用 list_long_term_preferences 查看真实保存的内容”，检查数据库状态。

最后在聊天 B 说“忘记我之前的 Python 讲解偏好”，应调用 `forget_long_term_preference`，然后同一聊天和新聊天里都不应再通过长期记忆看到这一条。要注意**同一聊天中之前的用户消息仍属于短期记忆**，即使长期记忆删除，模型仍可能根据聊天历史回忆起旧说法，所以删除长期记忆应以数据库中的内容为准，而不是只看模型是否还答得出来。

### 验收用语（顺序执行）

1. **聊天 A**：`请记住：以后解释 Python 代码时要提供详细的中文注释。请调用 save_long_term_preference，key 用 python_explanation_style。`
2. **聊天 A**：`请将刚才的偏好改成：先给一个最简单的可运行示例，再解释原理；仍使用 python_explanation_style 这个 key。`
3. **新聊天 B**：`请列出我保存的长期偏好，并说出 python_explanation_style 的当前值。`
4. **聊天 B**：`请调用 forget_long_term_preference，删除 python_explanation_style。`
5. **新聊天 C**：`请调用 list_long_term_preferences，确认现在是否还保存着 python_explanation_style。`

**局限说明**：

- 系统提示词会指导模型调用工具，但真实 Qwen 模型是否始终选对工具与稳定 key，仍需人工联调，不能仅凭模拟模型单元测试就宣称 100% 可靠。
- 敏感内容拦截是**有限的关键字防护**，不能代替完整的隐私保护、用户身份认证、审计和外部访问控制。
- `save_long_term_preference` 管理的是 AGENTS.md 内的专门小节；用户或模型用 `edit_file` 对其他段落做的编辑，不会被这个工具的列表自动归档。
- 同一用户对 AGENTS.md 进行大量并发读改写时，专用工具与通用文件工具之间还没有完善的跨进程冲突控制。正式多用户部署前需要补充。
- 现有 MongoDB Memory 没有被删除，但本阶段没有将两套自动写入逻辑同时启用，以避免存储冲突。
