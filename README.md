# WeChat Mac Chat History Exporter

> [中文](#中文文档) | [English](#english-documentation)

---

## 中文文档

一个完整的 macOS 微信聊天记录导出与 AI 智能 Agent 分析工具。

### 功能概览

| 功能 | 说明 |
|------|------|
| 🔓 一键解密 | 网页引导式操作，无需跳到终端 |
| 💬 聊天浏览 | 左右气泡展示，显示头像和昵称 |
| 📜 无限滚动 | 往上拉自动加载历史消息，跨数据库分片 |
| 🔄 增量同步 | 一键拉取最新消息（含 WAL 解密支持） |
| 📦 批量导出 | 支持 HTML / TXT / CSV 格式 |
| 🤖 AI 分析 | 集成 OpenRouter 400+ 模型，支持多模态、思考模式、Session 持久化 |
| ⚡ Agent Harness | 工具调用循环、计划执行、定时任务、自定义工具、MCP 集成 |
| 🖼️ 图片展示 | 2025 年及之前的图片可直接显示 |
| 🔗 链接跳转 | 公众号文章和新闻链接可直接点击 |

### 技术架构

```
wechat-extract-mac/
├── app.py                  # Flask 主应用 (端口 9527)
├── ai_router.py            # AI 多模型路由器 (OpenRouter + 直连 Provider)
├── ai_agent.py             # Agent 核心：ToolRegistry + run_agent_loop + run_plan_and_execute_loop
├── ai_memory.py            # 跨会话记忆：insights 提炼 + 自动注入
├── ai_summary.py           # 联系人智能摘要（后台批量生成）
├── ai_scheduler.py         # 定时 Agent 任务调度器
├── ai_custom_tools.py      # 用户自定义工具（prompt 模板驱动）
├── ai_semantic.py          # 语义向量搜索（可选，需 chromadb）
├── mcp_server.py           # MCP stdio server（供 Claude Desktop/Code 调用）
├── decrypt_core.py         # 解密引擎
├── scraper.py              # 公众号文章爬虫
├── config/                 # 运行时配置与数据（.gitignore）
│   ├── ai_config.json
│   ├── ai_sessions.json
│   ├── ai_memory.json
│   ├── scheduled_tasks.json
│   ├── custom_tools.json
│   └── contacts_cache.json
├── templates/
│   ├── index.html          # 导出主页面
│   ├── ai.html             # AI 分析页面
│   ├── agent.html          # Agent 控制台
│   └── scraper.html        # 公众号分析页面
└── tests/
    ├── test_ai_router.py   # 42 tests
    ├── test_agent_harness.py # 67 tests
    └── test_scraper.py
```

### 快速开始

**前置条件**

- macOS (Apple Silicon / Intel)
- Python 3.10+
- WeChat Mac 4.x 已安装并登录
- [uv](https://docs.astral.sh/uv/) 已安装

**启动**

```bash
cd wechat-extract-mac
./run.sh
```

或手动启动：

```bash
uv sync
sudo $(uv run python -c "import sys; print(sys.executable)") app.py
```

打开浏览器访问：**http://127.0.0.1:9527**

> ⚠️ 需要 `sudo` 权限用于：重签名微信、LLDB 密钥捕获、解密数据库

**首次使用流程（全部在网页完成）**

1. **重签名微信** — 页面点击按钮
2. **重启微信** — 完全退出 (Cmd+Q) 再重新打开
3. **捕获密钥** — 点击按钮，在微信中退出登录再重新登录
4. **解密数据库** — 点击按钮，等待进度完成后自动跳转

### Agent Harness

v4.0 引入完整 Agent 能力，让 AI 从被动问答变为主动智能 Agent。

**对话中的工具调用**

在 AI 分析页面选择联系人后，启用「🛠️ 工具调用」，AI 可主动调用：
- `search_messages` — 全文搜索聊天记录
- `get_messages_in_timerange` — 按时间段查询
- `get_contact_stats` — 统计消息数量
- `get_contacts_list` — 获取联系人列表
- `semantic_search` — 语义搜索（需安装 chromadb）

**计划模式**

启用「📋 计划模式」，AI 在执行前先生成可见的步骤计划，逐步执行更透明。

**定时任务（Agent 控制台 `/agent`）**

```
每天 08:00 → 汇总昨日聊天要点
每次同步后 → 分析新增消息
每周一 → 生成本周关系报告
```

**MCP 集成**

```bash
python mcp_server.py
```

在 Claude Desktop / Claude Code 中配置：

```json
{
  "mcpServers": {
    "wechat": {
      "command": "python",
      "args": ["/path/to/wechat-extract-mac/mcp_server.py"]
    }
  }
}
```

**语义搜索（可选）**

```bash
# 需要 Python 3.11+
pip install "wechat-extract-mac[semantic]"
# 或
pip install chromadb
```

启用后在 Agent 控制台 → 语义索引 tab 为联系人建立索引。

### AI 配置

右上角 ⚙️ 按钮，支持两种方式：

**方式一：[OpenRouter](https://openrouter.ai)（推荐）** — 一个 Key 访问 400+ 模型

**方式二：自定义接口** — 直接配置 api_base + api_key + model（Kimi、OpenAI、DeepSeek 等）

### 安全措施

| 措施 | 说明 |
|------|------|
| 🔒 敏感文件保护 | `.gitignore` 排除 API Key、Session、密钥等文件 |
| 🛡️ Debug 关闭 | 生产模式不启用调试器 |
| 🚫 XSS 防御 | 消息内容 HTML 转义后渲染 |
| 🌐 SSRF 防御 | AI API Base URL 限制为 HTTPS + 域名白名单 |
| 🏠 本地绑定 | 仅监听 `127.0.0.1` |

### Changelog

> 📋 完整更新日志：**[CHANGELOG.md](CHANGELOG.md)**

- **v4.0 (2026-08-25)** — Agent Harness：工具调用循环、定时任务、MCP 集成、语义搜索
- **v3.1 (2026-08-10)** — 全局 AI 设置、免费模型健康检查
- **v3 (2026-08-10)** — 多模型路由器 + OpenRouter 集成
- **v2 (2026-08-09)** — Dark/Light 主题、PDF 导出优化

### License

仅供个人数据备份使用。请勿用于任何非法用途。

### Credits

- 解密引擎集成自 [wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)
- 灵感来自 [WeChatMsg](https://github.com/LC044/WeChatMsg)

---

## English Documentation

A complete macOS WeChat chat history exporter and AI Agent analysis tool.

### Features

| Feature | Description |
|---------|-------------|
| 🔓 One-click Decrypt | Web-guided flow, no terminal required |
| 💬 Chat Viewer | Bubble layout with avatars and nicknames |
| 📜 Infinite Scroll | Auto-loads history, spans database shards |
| 🔄 Incremental Sync | One-click sync of new messages (WAL support) |
| 📦 Bulk Export | HTML / TXT / CSV formats |
| 🤖 AI Analysis | OpenRouter 400+ models, multimodal, thinking mode, session persistence |
| ⚡ Agent Harness | Tool-calling loop, plan-execute, scheduled tasks, custom tools, MCP |
| 🖼️ Image Display | Images from 2025 and earlier render inline |
| 🔗 Link Navigation | Article and news links are clickable |

### Architecture

```
wechat-extract-mac/
├── app.py                  # Flask app (port 9527)
├── ai_router.py            # Multi-provider AI router (OpenRouter + direct)
├── ai_agent.py             # Agent core: ToolRegistry + run_agent_loop + plan-execute
├── ai_memory.py            # Cross-session memory: extract insights + inject into prompts
├── ai_summary.py           # Contact intelligence summaries (background generation)
├── ai_scheduler.py         # Scheduled agent task engine
├── ai_custom_tools.py      # User-defined tools via prompt templates
├── ai_semantic.py          # Semantic vector search (optional, requires chromadb)
├── mcp_server.py           # MCP stdio server for Claude Desktop/Code
├── decrypt_core.py         # Decryption engine
├── scraper.py              # WeChat Official Account article crawler
├── config/                 # Runtime config & data (.gitignored)
├── templates/
│   ├── index.html          # Export page
│   ├── ai.html             # AI chat analysis page
│   ├── agent.html          # Agent dashboard
│   └── scraper.html        # Article analysis page
└── tests/                  # 109 tests total
```

### Quick Start

**Prerequisites**

- macOS (Apple Silicon or Intel)
- Python 3.10+
- WeChat Mac 4.x installed and logged in
- [uv](https://docs.astral.sh/uv/) installed

**Run**

```bash
cd wechat-extract-mac
./run.sh
```

Or manually:

```bash
uv sync
sudo $(uv run python -c "import sys; print(sys.executable)") app.py
```

Open your browser at **http://127.0.0.1:9527**

> ⚠️ `sudo` is required for: re-signing WeChat, LLDB key capture, database decryption

**First-time Setup (all in the browser)**

1. **Re-sign WeChat** — click the button on the setup page
2. **Restart WeChat** — fully quit (Cmd+Q) then reopen
3. **Capture key** — click the button, then log out and back in to WeChat
4. **Decrypt databases** — click the button and wait

### Agent Harness

v4.0 introduces a full agent harness inspired by DeepSeek Harness (dsh), turning AI from passive Q&A into an active data agent.

**Tool-Calling in Chat**

Select a contact in the AI page, enable "🛠️ Tool Calls" — the AI can now actively call:
- `search_messages` — full-text search across chat history
- `get_messages_in_timerange` — query by time range
- `get_contact_stats` — message count statistics
- `get_contacts_list` — list contacts with filtering
- `semantic_search` — semantic similarity search (requires chromadb)

**Plan-and-Execute Mode**

Enable "📋 Plan Mode" — the AI generates a visible numbered plan before executing, making multi-step reasoning transparent.

**Scheduled Tasks (Agent Dashboard `/agent`)**

```
daily@08:00   → Summarize yesterday's chats
on_sync       → Analyze newly synced messages
weekly@mon    → Generate weekly relationship report
interval@6h   → Any recurring task
```

**MCP Integration**

```bash
python mcp_server.py
```

Configure in Claude Desktop or Claude Code `.claude/settings.json`:

```json
{
  "mcpServers": {
    "wechat": {
      "command": "python",
      "args": ["/path/to/wechat-extract-mac/mcp_server.py"]
    }
  }
}
```

Exposes 5 tools: `search_messages`, `get_messages_in_timerange`, `get_contact_stats`, `get_contacts_list`, `get_message_count`.

**Semantic Search (optional)**

```bash
# Requires Python 3.11+
pip install chromadb
```

Once installed, go to Agent Dashboard → Semantic Index tab to index a contact. The `semantic_search` tool is automatically registered in the agent.

**User-Defined Custom Tools**

In Agent Dashboard → Custom Tools, define tools with name + description + parameter schema + prompt template. No code required. The agent can call them just like built-in tools.

**Cross-Session Memory**

After each conversation with 4+ messages, key insights are extracted asynchronously and stored in `config/ai_memory.json`. The next session with the same contact automatically receives these memories in the system prompt. View and delete memories in Settings → 🧠 Memory tab.

### AI Setup

Click ⚙️ in the top bar:

**Option 1: [OpenRouter](https://openrouter.ai) (recommended)** — one API key for 400+ models (GPT-4o, Claude, DeepSeek, Gemini, etc.)

**Option 2: Custom endpoint** — configure api_base + api_key + model directly (Kimi, OpenAI, DeepSeek, etc.)

### Decryption Technical Details

WeChat Mac 4.x uses SQLCipher 4 to encrypt local databases:
- **Algorithm**: AES-256-CBC
- **Page size**: 4096 bytes
- **KDF**: PBKDF2-HMAC-SHA512, 256,000 iterations
- **HMAC**: SHA-512, 64 bytes per page

Key extraction process:
1. Re-sign WeChat with ad-hoc to remove Hardened Runtime
2. Set LLDB breakpoint on `CCKeyDerivationPBKDF`
3. Trigger key derivation via logout/login
4. Read 32-byte passphrase from ARM64 register `$x1`
5. Derive encryption key: passphrase + per-DB salt via PBKDF2

### Security

| Measure | Description |
|---------|-------------|
| 🔒 Secrets excluded | `.gitignore` covers API keys, sessions, key files |
| 🛡️ Debug off | Werkzeug debugger disabled in production |
| 🚫 XSS prevention | Message content HTML-escaped before rendering |
| 🌐 SSRF protection | API base URL restricted to HTTPS + domain allowlist |
| 🏠 Local only | Binds to `127.0.0.1` only |

### Dependencies

```bash
uv sync          # installs from pyproject.toml
# or
pip install -r requirements.txt
```

Optional:
```bash
pip install chromadb   # semantic search (Python 3.11+)
pip install mcp        # already included in pyproject.toml
```

### Changelog

> 📋 Full changelog: **[CHANGELOG.md](CHANGELOG.md)**

- **v4.0 (2026-08-25)** — Agent Harness: tool-calling loop, scheduler, MCP, semantic search, 67 new tests
- **v3.1 (2026-08-10)** — Global AI settings, free model health check
- **v3 (2026-08-10)** — Multi-provider router + OpenRouter integration
- **v2 (2026-08-09)** — Dark/Light theme, native PDF export

### License

Personal data backup use only. Do not use for any illegal purposes.

### Credits

- Decryption engine from [wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)
- Inspired by [WeChatMsg](https://github.com/LC044/WeChatMsg)
