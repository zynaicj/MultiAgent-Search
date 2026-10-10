# DeepAgents 多轮聊天功能（开发分支）

## 已实现
- 复用现有 `create_deep_agent` 和 SQLite `SqliteSaver` Checkpointer：同一个 conversation/thread_id 可继续对话。
- 保留原有三个 SubAgent、工具调用、报告生成和 WebSocket 执行日志。
- 会话数据库 `data/conversations.db` 存储会话元数据、用户消息与成功完成的最终答案。
- 提供新建、列出、重命名、删除会话与读取历史的接口。
- 前端连续对话、历史会话切换、文件上传与下载。
- 对每个会话禁止同一进程内并发运行，以降低 Checkpoint 冲突。
- 保留 DeepAgents 0.4.3 内置规划、文件系统与自动摘要中间件（未替换内置 middleware）。

## 运行

保留既有 `.env` 配置和依赖。在项目根目录执行：

```powershell
python api/server.py
```

打开 http://127.0.0.1:8000 。运行之前请确保模型密钥、网络检索工具等原有依赖均可用。

## 手动验收

1. 新建聊天，问“什么是 LangGraph？”，再问“它和 DeepAgents 有何区别？”；检查第二轮是否参考第一轮。
2. 新建第二个会话，问无关问题；切回第一个会话并继续提问。
3. 重启服务，打开历史会话继续提问，验证 Checkpoint 恢复。
4. 上传文件并要求 Agent 读取；后续追问生成的报告，检查工作目录是否复用。
5. 在模型执行中再次点击发送，应阻止重复提交；完成后才能继续。
6. 重命名与删除会话，刷新页面确认持久化操作。

## API

- `GET /api/conversations`
- `POST /api/conversations`
- `GET /api/conversations/{cid}/messages`
- `PATCH /api/conversations/{cid}` body `{"title":"新名称"}`
- `DELETE /api/conversations/{cid}`
- `POST /api/task` body `{"thread_id":"会话 ID","query":"问题"}`

## 当前边界

- 不包含身份验证或多用户权限控制，仅适用于本地单用户使用。
- 状态锁仅在单服务进程内生效，不适用于多 worker 并发部署。
- 长对话自动摘要属于 DeepAgents 内置行为，其触发阈值依模型版本配置。
- 并未启用 HITL 审批，因为本项目已有文件写入工具，审批要求应当按工具逐项设计，不适合无差别全局拦截。
- 运行环境尚需实际联调验证，包括子 Agent、模型调用、上传工具与生成文件。
