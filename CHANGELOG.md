# Changelog

## 2026-08-25 (v4.0) — Agent Harness

### 核心 Agent 架构
- **工具调用 Agent Loop** (`ai_agent.py`) — 借鉴 DeepSeek Harness (dsh) 插件理念，引入 `ToolRegistry`（插件式工具注册）+ `run_agent_loop`（ReAct 循环）。AI 不再被动等待，可主动调用工具查询聊天数据
  - 内置 4 个工具：`search_messages`（全文搜索）、`get_messages_in_timerange`、`get_contact_stats`、`get_contacts_list`
  - 新增 `search_messages` 数据函数（LIKE 跨分片搜索）
- **Plan-and-Execute 模式** — 复杂任务先生成可见执行计划（PLAN/END_PLAN 格式），再逐步执行，前端实时展示计划步骤进度
- **工具审批机制** — `mark_requires_approval` 标记敏感工具，执行前弹出确认 modal，30 秒超时自动批准；新增 `POST /api/ai/tool_approval` 端点

### 智能记忆与摘要
- **跨会话记忆** (`ai_memory.py`) — 对话结束后异步提炼关键 insights，存入 `config/ai_memory.json`；下次同联系人对话自动注入系统提示
  - 新增 `GET/DELETE /api/ai/memory` 端点
  - 设置模态框新增「🧠 记忆」tab：查看/删除每个联系人的历史记忆
- **联系人智能摘要** (`ai_summary.py`) — 后台 ThreadPoolExecutor 批量生成联系人一句话描述，持久化到 `contacts_cache.json`
  - 新增 `POST/GET /api/contacts/summaries` 端点
  - 联系人卡片显示 AI 描述
  - 首页新增「✨ 生成摘要」按钮
- **同步后智能通知** — sync 成功后后台 LLM 判断是否有重要消息，推送 `important_message` SSE 事件；可在「🔔 通知」tab 关闭

### 定时 Agent（自主任务）
- **AI 任务调度器** (`ai_scheduler.py`) — 纯 threading 实现，无额外依赖
  - 支持 `daily@HH:MM`、`weekly@DOW@HH:MM`、`interval@Nh`、`on_sync`（每次同步后触发）四种调度格式
  - 任务执行历史（最近 10 次），SSE 实时推送结果
  - 新增 5 个路由：`GET/POST /api/agent/tasks`、`PUT/DELETE/POST(run) /api/agent/tasks/<id>`

### 用户自定义工具（Skill）
- **自定义工具注册** (`ai_custom_tools.py`) — 用户通过 UI 定义工具（名称 + 描述 + 参数 schema + prompt 模板），无需写代码即可扩展 Agent 能力
  - 模板支持 `{参数名}` 和 `{messages}` 占位符
  - 新增 `GET/POST /api/agent/custom_tools`、`DELETE /api/agent/custom_tools/<id>`

### 语义搜索
- **向量语义索引** (`ai_semantic.py`) — 基于 ChromaDB（可选依赖，`pip install "wechat-extract-mac[semantic]"`），搜"项目"能找到"工程"
  - `semantic_search` 工具自动注册到 Agent（当 chromadb 可用时）
  - 新增 `GET /api/agent/semantic/status`、`POST /api/agent/semantic/index`

### MCP 集成
- **MCP Server** (`mcp_server.py`) — 将 5 个微信数据函数暴露为 MCP 工具，Claude Desktop / Claude Code 可直接调用
  - 运行：`python mcp_server.py`；配置：`mcpServers.wechat` 指向此脚本

### 公众号分析增强
- 文章分析支持 `mode=agent`：AI 按需拉取文章完整内容，不再全量截断
- 前端新增「📋 标准」/「🤖 智能体」模式选择器

### Agent 控制台（新页面）
- 新增 `/agent` 页面（`templates/agent.html`）— 统一 Agent 管理界面，5 个 tab：
  - **任务调度**：创建/启停/手动触发/查看历史
  - **自定义工具**：管理用户定义的 Agent 工具
  - **语义索引**：查看索引状态，触发联系人索引
  - **生成报告**：月度总结/关系分析/自定义 HTML 报告
  - **执行记录**：所有 Agent 任务的完整执行 trace（本地 localStorage 持久化）
- 所有页面顶部导航加入「⚡ Agent」链接

### AI 对话界面优化
- 新增「📋 计划模式」toggle，展示执行计划步骤（可折叠）
- 工具调用进度面板支持 session 恢复（重新打开历史对话时「查看推理过程」保留完整记录）
- 修复 `useToolsToggle` checkbox 重复渲染 bug
- 设置模态框新增「🔔 通知」tab（重要消息提醒开关）

### 测试
- 新增 `tests/test_agent_harness.py` — 67 个测试，覆盖 `ToolRegistry`、`run_agent_loop`、`run_plan_and_execute_loop`、`ai_memory`、`ai_scheduler`、`ai_custom_tools`、Flask API 端点，全部通过（3.45s）

### 依赖
- 新增必选：`mcp>=1.0.0`
- 新增可选：`chromadb>=0.5.0`（语义搜索，需 Python 3.11+）

## 2026-08-10 (v3.1)

- **全局 AI 设置** — 新增 `static/ai-settings.js` 共享组件，所有页面（首页、AI、公众号分析）均可通过右上角按钮配置 AI
- **免费模型健康检查** — 后台线程每 5 分钟自动测试免费模型可用性
  - 模型下拉显示实时状态图标（可用/较慢/不可用）
  - 可用模型自动排序到前面，附带响应延迟
  - 新增 `GET /api/ai/models/status` 端点查看健康状态
- **模型分组优化** — OpenRouter 模型按免费/付费分组展示，显示可用数量
- **错误码体系** — 新增 `AIError` 异常类 + 16 个中文错误码，所有 API 错误返回 `{error, error_code}`
- **修复：聊天预览失效** — `eventSource` 变量声明顺序导致 TDZ 错误，所有 JS 事件监听器未注册
- **修复：OpenRouter Key 保存失败** — `saveOpenrouterConfig` 改用 `add_provider`（兼容 provider 不存在的情况）

## 2026-08-10 (v3)

- **多模型路由器** — 新增 `ai_router.py` 模块，支持多 Provider 配置和动态模型切换
  - **OpenRouter 集成** — 一个 API Key 访问 400+ 模型（GPT-4o、Claude、DeepSeek、Gemini 等）
  - **模型发现** — 调用 `/v1/models` 动态获取可用模型列表，自动填充下拉选择器
  - **自定义接口兜底** — 无 OpenRouter 时可手动配置 api_base + api_key + model（保留旧方案）
  - **模型下拉选择器** — Top bar 新增 `<select>` 按 Provider 分组，显示能力标签（🧠 thinking, 👁 vision）
  - **Per-request 模型选择** — 每次请求可指定不同模型，无需频繁改设置
  - **统一 SSE 流式处理** — 提取 `stream_chat_sse()` 统一函数，消除两个端点间的重复代码（~200行）
  - **自动配置迁移** — 旧格式 `{api_base, api_key, model}` 启动时自动迁移为新的多 Provider 格式
  - **OpenRouter 专用 headers** — 自动添加 `HTTP-Referer` 和 `X-OpenRouter-Title`
  - **SSRF 防护增强** — 允许列表包含 `openrouter.ai`，且从配置的 Provider 域名动态扩展
- **设置界面重构** — 双 Tab 布局：「OpenRouter (推荐)」+ 「自定义接口」
- **新增 API 端点** — `GET /api/ai/models`、`POST /api/ai/models/discover`
- **新增测试** — `tests/test_ai_router.py`（42 个测试覆盖路由、迁移、SSRF、Provider 管理）

## 2026-08-09 (v2)

- **全局 Dark/Light 主题** — 新增 `static/theme.js` 共享模块，index / ai / scraper 三个页面统一支持主题切换
  - 侧边栏、主内容区、聊天气泡、toast 等全部适配双主题
  - 自动检测系统 `prefers-color-scheme` 偏好，首次访问跟随 OS
  - 主题选择持久化到 `localStorage`，跨页面同步
- **PDF 导出优化** — 移除 html2pdf.js，改用浏览器原生 `window.print()` 导出
  - 矢量文字可搜索、图表清晰、文件更小
  - 保留原始主题样式（dark 模式输出 dark PDF）
  - 注入 `break-inside: avoid` 防止图表被分页切割
  - AI Prompt 指导使用 SVG 渲染器、固定图表高度、A4 宽度
- **联系人 JSON 缓存** — 新增 `/api/contacts/cached` 端点
  - 首次加载直接读 JSON 文件（毫秒级响应）
  - 后台同步完成后自动重建缓存
  - 缓存损坏时自动降级为数据库查询
- **config/ 目录整理** — 所有运行时 JSON 文件迁移到 `config/` 目录统一管理
- **输入框优化** — 公众号分析页 follow-up 输入框加大，支持拖拽调整高度（最大 240px）
- **测试迁移** — `test_scraper.py` 迁入 `tests/` 包，新增 `tests/test_pdf_export.py`（25 个测试）
- **样式修复** — 移除所有硬编码颜色，全部使用 CSS 变量驱动

## 2026-08-09

- **联系人分组** — 侧边栏联系人按「💬 聊天」和「📰 公众号」分组显示，公众号组默认折叠可展开
- **实时推送更新** — 新增 `/api/events` SSE 长连接端点，后台同步完成后实时推送到前端刷新联系人列表（替代轮询），支持多标签页同步
- **公众号文章分析** — 新增 `/scraper` 页面，完整的多步骤工作流：
  - 选择公众号 → 提取文章链接 → 选择文章 → 配置 AI Prompt → 爬取 → AI 可视化分析
  - 时间线模式：所有步骤同时可见，每步可折叠
- **文章列表分页 & 时间筛选** — 每页 50 篇，支持「全部 / 本周 / 本月 / 今年」快速筛选，默认按时间由近到远排序，默认不全选
- **文章爬虫模块** — 新增 `scraper.py`，requests + BeautifulSoup 抓取微信公众号文章正文
  - User-Agent 轮换模拟微信浏览器、指数退避重试、速率限制防封
  - subprocess 模式运行，SSE 流式进度反馈
  - 验证码/反爬重定向检测，详细错误分类
- **爬虫单元测试** — 新增 `test_scraper.py`，29 个测试用例覆盖：
  - 成功提取、过期/删除文章、反爬验证码检测、HTTP 错误、网络超时、批量爬取、内容解析
- **爬取统计报告** — 爬取过程实时展示成功/失败统计，完成后显示每篇文章详细状态和失败原因
- **AI 分析全流程展示** — 类似 ai.html 的完整流式输出：
  - 思考过程（collapsible）→ Markdown 实时渲染 → HTML 可视化结果
  - 分析进行中禁用发送按钮，防止重复请求
- **多轮对话** — 分析结果下方提供输入框，用户可继续调整分析方向
- **Session 持久化** — 分析会话保存到 `scraper_sessions.json`，支持历史切换和恢复
- **PDF 下载** — HTML 分析结果支持一键导出 PDF（html2pdf.js）
- **文章链接提取 API** — 新增 `/api/articles/urls/<username>` 和批量提取端点
- **后台同步无弹窗** — 点击同步无确认弹窗，后台每 10 秒自动同步（幂等），同步完成实时刷新列表
- **新增依赖** — `scrapy>=2.11.0`、`beautifulsoup4>=4.12.0`

## 2026-08-08

- **uv 依赖管理** — 新增 `pyproject.toml` 与 `uv.lock`，默认通过 `./run.sh` 启动
- **保留 pip 兼容** — 保留 `requirements.txt`，支持 `pip3 install -r requirements.txt`
- **一键启动脚本** — 新增 `run.sh`，自动 `uv sync` 并用 sudo 启动应用

## 2026-08-04

- **智能时间范围选择器** — 选中大量消息的联系人时，自动提示选择分析时间段（7天/30天/3个月/半年/自定义），避免撑爆 AI context
- **跨消息时间范围查询** — 新增 `get_messages_by_timerange()` 支持按时间段从所有 DB 分片中查询
- **消息统计 API** — `/api/messages/<username>/stats` 返回消息总数和日期范围
- **消息量超限自动采样** — 选"全部"时均匀采样（而非截断），保留时间分布特征
- **移除后端硬性截断** — context 大小由前端时间范围控制，不再强制截断

## 2026-08-03 ~ 2026-08-04

- **安全修复** — XSS 防御、SSRF 防御、路径穿越防御、关闭 debug、文件权限、上传限制
- **跨数据库消息加载** — 同一联系人跨 6 个 DB 分片的消息合并展示（如"老婆" 50,000+ 条）
- **动态 DB 发现** — 自动检测新增的 message_N.db 分片
- **WAL 解密支持** — 解密 .db-wal 文件获取最新未提交数据
- **消息左右分开** — 正确识别自己 vs 对方（基于 Name2Id rowid 匹配）
- **发送者昵称** — 群聊和 1v1 都显示备注名（非 wxid）
- **头像显示** — 对方消息左侧显示头像
- **无限滚动** — 往上拉自动加载历史消息
- **链接可点击** — 公众号文章超链接、mmreader 新闻聚合正确解析
- **公众号标识** — 联系人列表标注"公众号"标签
- **AI Session 持久化** — 对话历史保存到文件，切换/刷新不丢失
- **深度思考模式** — Kimi K3 推理过程中文展示，可开关
- **多模态上传** — 支持图片（base64）和文件（PDF/Excel）发给 AI
- **自动重试** — API overload 时自动重试 3 次
- **中文编码修复** — SSE 流式响应正确显示中文
- **端口修改** — 改为 9527（避免 macOS AirPlay 占用 5000）
- **同步提醒** — 提示用户重启微信再同步（确保 WAL 合并）

## 2026-07-31

- **初始版本** — 完整的 Web 应用：解密引导、聊天浏览、批量导出、AI 分析
- **集成 decrypt_core** — 从 wcdb-key-tool 整合解密方案，全流程网页操作
- **Kimi K3 集成** — 流式 AI 对话分析聊天记录
