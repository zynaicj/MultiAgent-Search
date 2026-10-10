# MultiAgent-Search：DeepAgents 连续聊天集成（开发分支）

基线：`feature/tool-governance-hitl`。原有三个专业 SubAgent、多 Agent 协作工作流、SQL 治理与 HITL、MongoDB Memory 保留。

## 本次集成

- 会话与每轮 Run 分离：同一 `conversation_id` 对应同一个 LangGraph `thread_id`。
- `data/conversations.db`：保存会话、消息、运行、事件。原有 `data/checkpoints.db` 保存 DeepAgents 状态，MongoDB 保存长期记忆。
- 同一会话串行，重复 `client_message_id` 不会重复执行任务；不同会话允许并发。
- DeepAgents 支持同会话追问、专业子 Agent、Markdown/PDF、MongoDB Recall、TodoList 监控、人工审批及恢复。
- 协作模式保留 Planner → Worker → Synthesizer → Reviewer，并使用最近的历史问答帮助解析追问。
- WebSocket 先建立再发送任务；历史事件通过 `/api/conversations/{id}/events` 重放；前端可重开历史聊天。
- 生成文件仍保存到真实目录 `output/session_{conversation_id}/`，附件暂存 `updated/session_{conversation_id}/`。
- 待审批状态写入聊天数据库；重启后尝试恢复到原有 SQL 审批注册表。重启时原本 running 的任务标记为失败，不自动重复执行高风险操作。

## 本地启动

```powershell
git fetch origin
git switch --track origin/feature/deepagents-chat-integrated
python -m compileall -q agent api tools
python -m unittest discover -s tests -p "test_chat_store.py" -v
python api/server.py
```

打开 http://127.0.0.1:8000 ，点击“＋”选择模式并新建聊天。

注意：启动前要确保模型 API 额度有效；需要长期记忆时还应连接 MongoDB。配置文件 `.env` 不能提交到 Git。

## 基础手动验收

1. 新建 DeepAgents 会话，提问“我的项目代号是蓝鲸27”，第二轮问“刚才项目代号是什么”，验证复用 Checkpoint。
2. 刷新页面、重启后端，用相同会话继续追问，确认历史聊天可加载。
3. 在同一会话生成 Markdown，再转换 PDF，确认文件在同一个 `session_{conversation_id}` 目录。
4. 在隔离的测试数据库上验证 SQL UPDATE 人工审批的拒绝与批准；中断后刷新页面，确认审批界面仍然显示。
5. 新建另一会话，确认不会继承上一会话的短期消息。
6. 新建“多 Agent 协作模式”会话，检查日志中的 Planner、Worker、Synthesizer、Reviewer 调度；追问上一轮对象。

## 核心 Chat API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / POST | `/api/conversations` | 历史列表 / 新建 |
| GET / PATCH / DELETE | `/api/conversations/{id}` | 会话详情 / 改名 / 删除 |
| GET / POST | `/api/conversations/{id}/messages` | 查看历史 / 发送 |
| GET | `/api/conversations/{id}/events` | 事件重放，支持 `after` |
| GET | `/api/conversations/{id}/files` | 会话真实产物列表 |
| GET | `/api/runs/{run_id}` | 查询运行 |
| POST | `/api/runs/{run_id}/approval` | 批准或拒绝 SQL 工具调用 |

发送一轮消息：`POST /api/conversations/{id}/messages`

```json
{"query":"继续上一个话题","client_message_id":"unique-client-id"}
```

返回 `run_id`，结果以 WebSocket 事件推送，也可以在会话消息 API 中查询。

## 已知边界

- 尚未在用户 Windows 环境完成 Qwen、Tavily、RAGFlow、MySQL、MongoDB 全链路实测。GitHub Actions 检查覆盖 Python 编译与聊天 SQLite 状态机单元测试，不等同于业务功能测试。
- 目前面向本地单用户开发；现有 MongoDB Memory 集合没有 `user_id` 隔离，不应直接作为多租户线上产品部署。
- 长期记忆以明确标注的上下文片段输入 DeepAgents；为兼容项目锁定的 `deepagents==0.4.3`，尚未新增依赖于更新版 Middleware 接口的动态 System Prompt 注入。
- 长对话摘要沿用 DeepAgents 内置机制，未额外叠加可能重复触发的 SummarizationMiddleware；摘要触发阈值、虚拟 Backends 与物理文件系统的实际行为需结合所装 0.4.3 版验证。
- 取消执行、跨进程分布式调度、正式身份认证、细粒度文件访问授权需要后续按生产环境需求加固。
