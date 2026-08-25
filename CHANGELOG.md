# Changelog

> [中文](#中文) | [English](#english)

---

## 中文

### 2026-08-25 (v4.0) — Agent Harness

#### 核心 Agent 架构
- **工具调用 Agent Loop** (`ai_agent.py`) — 借鉴 DeepSeek Harness (dsh) 插件理念，引入 `ToolRegistry`（插件式工具注册）+ `run_agent_loop`（ReAct 循环）。AI 不再被动等待，可主动调用工具查询聊天数据
  - 内置 4 个工具：`search_messages`（全文搜索）、`get_messages_in_timerange`、`get_contact_stats`、`get_contacts_list`
  - 新增 `search_messages` 数据函数（LIKE 跨分片搜索）
- **Plan-and-Execute 模式** — 复杂任务先生成可见执行计划（PLAN/END_PLAN 格式），再逐步执行，前端实时展示计划步骤进度
- **工具审批机制** — `mark_requires_approval` 标记敏感工具，执行前弹出确认 modal，30 秒超时自动批准；新增 `POST /api/ai/tool_approval` 端点

#### 智能记忆与摘要
- **跨会话记忆** (`ai_memory.py`) — 对话结束后异步提炼关键 insights，存入 `config/ai_memory.json`；下次同联系人对话自动注入系统提示
  - 新增 `GET/DELETE /api/ai/memory` 端点
  - 设置模态框新增「🧠 记忆」tab：查看/删除每个联系人的历史记忆
- **联系人智能摘要** (`ai_summary.py`) — 后台 ThreadPoolExecutor 批量生成联系人一句话描述，持久化到 `contacts_cache.json`
  - 新增 `POST/GET /api/contacts/summaries` 端点
  - 联系人卡片显示 AI 描述
  - 首页新增「✨ 生成摘要」按钮
- **同步后智能通知** — sync 成功后后台 LLM 判断是否有重要消息，推送 `important_message` SSE 事件；可在「🔔 通知」tab 关闭

#### 定时 Agent（自主任务）
- **AI 任务调度器** (`ai_scheduler.py`) — 纯 threading 实现，无额外依赖
  - 支持 `daily@HH:MM`、`weekly@DOW@HH:MM`、`interval@Nh`、`on_sync`（每次同步后触发）四种调度格式
  - 任务执行历史（最近 10 次），SSE 实时推送结果
  - 新增 5 个路由：`GET/POST /api/agent/tasks`、`PUT/DELETE/POST(run) /api/agent/tasks/<id>`

#### 用户自定义工具（Skill）
- **自定义工具注册** (`ai_custom_tools.py`) — 用户通过 UI 定义工具（名称 + 描述 + 参数 schema + prompt 模板），无需写代码即可扩展 Agent 能力
  - 模板支持 `{参数名}` 和 `{messages}` 占位符
  - 新增 `GET/POST /api/agent/custom_tools`、`DELETE /api/agent/custom_tools/<id>`

#### 语义搜索
- **向量语义索引** (`ai_semantic.py`) — 基于 ChromaDB（可选依赖，`pip install "wechat-extract-mac[semantic]"`），搜"项目"能找到"工程"
  - `semantic_search` 工具自动注册到 Agent（当 chromadb 可用时）
  - 新增 `GET /api/agent/semantic/status`、`POST /api/agent/semantic/index`

#### MCP 集成
- **MCP Server** (`mcp_server.py`) — 将 5 个微信数据函数暴露为 MCP 工具，Claude Desktop / Claude Code 可直接调用
  - 运行：`python mcp_server.py`；配置：`mcpServers.wechat` 指向此脚本

#### 公众号分析增强
- 文章分析支持 `mode=agent`：AI 按需拉取文章完整内容，不再全量截断
- 前端新增「📋 标准」/「🤖 智能体」模式选择器

#### Agent 控制台（新页面）
- 新增 `/agent` 页面（`templates/agent.html`）— 统一 Agent 管理界面，5 个 tab：
  - **任务调度**：创建/启停/手动触发/查看历史
  - **自定义工具**：管理用户定义的 Agent 工具
  - **语义索引**：查看索引状态，触发联系人索引
  - **生成报告**：月度总结/关系分析/自定义 HTML 报告
  - **执行记录**：所有 Agent 任务的完整执行 trace（本地 localStorage 持久化）
- 所有页面顶部导航加入「⚡ Agent」链接

#### AI 对话界面优化
- 新增「📋 计划模式」toggle，展示执行计划步骤（可折叠）
- 工具调用进度面板支持 session 恢复（重新打开历史对话时「查看推理过程」保留完整记录）
- 修复 `useToolsToggle` checkbox 重复渲染 bug
- 设置模态框新增「🔔 通知」tab（重要消息提醒开关）

#### 测试
- 新增 `tests/test_agent_harness.py` — 67 个测试，覆盖 `ToolRegistry`、`run_agent_loop`、`run_plan_and_execute_loop`、`ai_memory`、`ai_scheduler`、`ai_custom_tools`、Flask API 端点，全部通过（3.45s）

#### 依赖
- 新增必选：`mcp>=1.0.0`
- 新增可选：`chromadb>=0.5.0`（语义搜索，需 Python 3.11+）

---

### 2026-08-10 (v3.1)

- **全局 AI 设置** — 新增 `static/ai-settings.js` 共享组件，所有页面（首页、AI、公众号分析）均可通过右上角按钮配置 AI
- **免费模型健康检查** — 后台线程每 5 分钟自动测试免费模型可用性
  - 模型下拉显示实时状态图标（可用/较慢/不可用）
  - 可用模型自动排序到前面，附带响应延迟
  - 新增 `GET /api/ai/models/status` 端点查看健康状态
- **模型分组优化** — OpenRouter 模型按免费/付费分组展示，显示可用数量
- **错误码体系** — 新增 `AIError` 异常类 + 16 个中文错误码，所有 API 错误返回 `{error, error_code}`
- **修复：聊天预览失效** — `eventSource` 变量声明顺序导致 TDZ 错误，所有 JS 事件监听器未注册
- **修复：OpenRouter Key 保存失败** — `saveOpenrouterConfig` 改用 `add_provider`（兼容 provider 不存在的情况）

---

### 2026-08-10 (v3)

- **多模型路由器** — 新增 `ai_router.py` 模块，支持多 Provider 配置和动态模型切换
  - **OpenRouter 集成** — 一个 API Key 访问 400+ 模型（GPT-4o、Claude、DeepSeek、Gemini 等）
  - **模型发现** — 调用 `/v1/models` 动态获取可用模型列表，自动填充下拉选择器
  - **自定义接口兜底** — 无 OpenRouter 时可手动配置 api_base + api_key + model
  - **统一 SSE 流式处理** — 提取 `stream_chat_sse()` 统一函数，消除重复代码（~200行）
  - **自动配置迁移** — 旧格式启动时自动迁移为新的多 Provider 格式
  - **SSRF 防护增强** — 包含 `openrouter.ai`，从配置的 Provider 域名动态扩展
- **设置界面重构** — 双 Tab 布局：「OpenRouter (推荐)」+ 「自定义接口」
- **新增 API 端点** — `GET /api/ai/models`、`POST /api/ai/models/discover`
- **新增测试** — `tests/test_ai_router.py`（42 个测试）

---

### 2026-08-09 (v2)

- **全局 Dark/Light 主题** — 新增 `static/theme.js`，index / ai / scraper 三个页面统一支持主题切换
- **PDF 导出优化** — 移除 html2pdf.js，改用浏览器原生 `window.print()`
- **联系人 JSON 缓存** — 新增 `/api/contacts/cached` 端点，首次加载毫秒级响应
- **config/ 目录整理** — 所有运行时 JSON 文件迁移到 `config/` 目录
- **测试迁移** — `test_scraper.py` 迁入 `tests/` 包，新增 `tests/test_pdf_export.py`（25 个测试）

---

### 2026-08-09 (v1)

- **联系人分组** — 侧边栏按「💬 聊天」和「📰 公众号」分组显示
- **实时推送更新** — 新增 `/api/events` SSE 长连接端点，后台同步完成后实时刷新
- **公众号文章分析** — 新增 `/scraper` 页面，完整多步骤工作流

---

## English

### 2026-08-25 (v4.0) — Agent Harness

#### Core Agent Architecture
- **Tool-Calling Agent Loop** (`ai_agent.py`) — Inspired by DeepSeek Harness (dsh) plugin philosophy. Introduces `ToolRegistry` (plugin-style tool registration) + `run_agent_loop` (ReAct loop). AI can now actively call tools to query chat data instead of passively waiting
  - 4 built-in tools: `search_messages` (full-text search), `get_messages_in_timerange`, `get_contact_stats`, `get_contacts_list`
  - New `search_messages` data function (LIKE search across database shards)
- **Plan-and-Execute Mode** — For complex tasks, AI first generates a visible execution plan (PLAN/END_PLAN format), then executes step by step. Frontend renders plan progress in real time
- **Tool Approval Mechanism** — `mark_requires_approval` marks sensitive tools; a confirmation modal appears before execution with a 30-second auto-approve timeout. New `POST /api/ai/tool_approval` endpoint

#### Intelligence Layer
- **Cross-Session Memory** (`ai_memory.py`) — After each conversation, key insights are extracted asynchronously into `config/ai_memory.json`; injected automatically into the system prompt for future sessions with the same contact
  - New `GET/DELETE /api/ai/memory` endpoints
  - New 🧠 Memory tab in settings modal: view and delete per-contact memory entries
- **Contact Intelligence Summaries** (`ai_summary.py`) — Background ThreadPoolExecutor generates one-line AI descriptions for contacts, persisted in `contacts_cache.json`
  - New `POST/GET /api/contacts/summaries` endpoints
  - Contact cards display AI summary text
  - New "✨ Generate Summaries" button on the index page
- **Smart Sync Notifications** — After a successful sync, a background LLM call classifies messages for importance and pushes an `important_message` SSE event. Can be disabled in the 🔔 Notifications tab

#### Scheduled Agents (Autonomous Tasks)
- **AI Task Scheduler** (`ai_scheduler.py`) — Pure threading implementation, no extra dependencies
  - 4 schedule formats: `daily@HH:MM`, `weekly@DOW@HH:MM`, `interval@Nh`, `on_sync` (fires after every sync)
  - Execution history (last 10 runs per task), results pushed via SSE
  - 5 new routes: `GET/POST /api/agent/tasks`, `PUT/DELETE/POST(run) /api/agent/tasks/<id>`

#### User-Defined Custom Tools (Skills)
- **Custom Tool Registration** (`ai_custom_tools.py`) — Users define tools via UI (name + description + parameter schema + prompt template) without writing code
  - Templates support `{param_name}` and `{messages}` placeholders
  - New `GET/POST /api/agent/custom_tools`, `DELETE /api/agent/custom_tools/<id>`

#### Semantic Search
- **Vector Semantic Index** (`ai_semantic.py`) — ChromaDB-backed (optional: `pip install "wechat-extract-mac[semantic]"`). Searching "project" finds "engineering", "task", etc.
  - `semantic_search` tool auto-registered in agent when chromadb is available
  - New `GET /api/agent/semantic/status`, `POST /api/agent/semantic/index`

#### MCP Integration
- **MCP Server** (`mcp_server.py`) — Exposes 5 WeChat data functions as MCP tools for Claude Desktop / Claude Code
  - Run: `python mcp_server.py`; configure `mcpServers.wechat` to point to this script

#### Article Analysis Enhancement
- New `mode=agent` for `POST /api/articles/analyze`: AI selectively fetches full article content instead of bulk truncation
- Frontend: new "📋 Standard" / "🤖 Agent" mode selector

#### Agent Dashboard (New Page)
- New `/agent` page (`templates/agent.html`) — Unified agent management UI with 5 tabs:
  - **Task Scheduler**: create, enable/disable, manually trigger, view history
  - **Custom Tools**: manage user-defined agent tools
  - **Semantic Index**: view index status, trigger per-contact indexing
  - **Generate Report**: monthly summary / relationship analysis / custom HTML reports
  - **Execution Log**: full agent execution trace (persisted in localStorage)
- Added "⚡ Agent" navigation link to all page headers

#### AI Chat UI Improvements
- New "📋 Plan Mode" toggle showing collapsible execution plan steps
- Tool call panel persists across session restores (the "View Reasoning" details panel reloads correctly after page refresh)
- Fixed duplicate `useToolsToggle` checkbox rendering bug
- New 🔔 Notifications tab in settings modal (toggle for important message alerts)

#### Tests
- New `tests/test_agent_harness.py` — 67 tests covering `ToolRegistry`, `run_agent_loop`, `run_plan_and_execute_loop`, `ai_memory`, `ai_scheduler`, `ai_custom_tools`, Flask API endpoints. All passing in 3.45s

#### Dependencies
- New required: `mcp>=1.0.0`
- New optional: `chromadb>=0.5.0` (semantic search, requires Python 3.11+)

---

### 2026-08-10 (v3.1)

- **Global AI Settings** — New `static/ai-settings.js` shared component; all pages (index, AI, scraper) can configure AI via the top-right ⚙️ button
- **Free Model Health Check** — Background thread tests free model availability every 5 minutes
  - Model dropdown shows real-time status icons (available / slow / unavailable)
  - Available models sorted to top with response latency shown
  - New `GET /api/ai/models/status` endpoint
- **Model Grouping** — OpenRouter models grouped by free/paid with count badges
- **Error Code System** — New `AIError` class + 16 error codes; all API errors return `{error, error_code}`
- **Fix: Chat preview broken** — `eventSource` TDZ error caused by variable declaration order; all JS event listeners were not registering
- **Fix: OpenRouter key save failure** — `saveOpenrouterConfig` now uses `add_provider` (handles missing provider)

---

### 2026-08-10 (v3)

- **Multi-Provider AI Router** — New `ai_router.py` module with multi-provider config and dynamic model switching
  - **OpenRouter Integration** — One API key for 400+ models (GPT-4o, Claude, DeepSeek, Gemini, etc.)
  - **Model Discovery** — Queries `/v1/models` to dynamically populate the model selector
  - **Custom Endpoint Fallback** — Manually configure api_base + api_key + model when no OpenRouter
  - **Unified SSE Streaming** — Extracted `stream_chat_sse()` shared function, eliminating ~200 lines of duplication
  - **Auto Config Migration** — Legacy `{api_base, api_key, model}` format auto-migrated on startup
  - **SSRF Protection** — Allowlist includes `openrouter.ai`, dynamically extended from configured provider domains
- **Settings UI Redesign** — Two-tab layout: "OpenRouter (recommended)" + "Custom Endpoint"
- **New API endpoints** — `GET /api/ai/models`, `POST /api/ai/models/discover`
- **New Tests** — `tests/test_ai_router.py` (42 tests)

---

### 2026-08-09 (v2)

- **Global Dark/Light Theme** — New `static/theme.js` shared module; all three pages support theme switching
- **PDF Export** — Removed html2pdf.js, switched to native browser `window.print()` for vector-quality output
- **Contact JSON Cache** — New `/api/contacts/cached` endpoint for millisecond-fast page loads
- **Config Directory** — All runtime JSON files moved to `config/`
- **Tests** — `test_scraper.py` moved into `tests/` package; new `tests/test_pdf_export.py` (25 tests)

---

### 2026-08-09 (v1)

- **Contact Grouping** — Sidebar shows "💬 Chats" and "📰 Official Accounts" groups
- **Real-time SSE Push** — New `/api/events` long-poll endpoint; sync completion pushes live updates
- **Official Account Article Analysis** — New `/scraper` page with full multi-step workflow
