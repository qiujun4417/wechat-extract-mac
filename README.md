# WeChat Mac Chat History Exporter

一个完整的 macOS 微信聊天记录导出与 AI 分析工具。

## 功能概览

| 功能 | 说明 |
|------|------|
| 🔓 一键解密 | 网页引导式操作，无需跳到终端 |
| 💬 聊天浏览 | 左右气泡展示，显示头像和昵称 |
| 📜 无限滚动 | 往上拉自动加载历史消息，跨数据库分片 |
| 🔄 增量同步 | 一键拉取最新消息（含 WAL 解密支持） |
| 📦 批量导出 | 支持 HTML / TXT / CSV 格式 |
| 🤖 AI 分析 | 集成 Kimi K3，支持多模态、思考模式、Session 持久化 |
| 🖼️ 图片展示 | 2025 年及之前的图片可直接显示 |
| 🔗 链接跳转 | 公众号文章和新闻链接可直接点击 |

## 技术架构

```
wechat-extract-mac/
├── app.py                 # Flask 主应用 (端口 9527)
├── decrypt_core.py        # 解密引擎 (从 wcdb-key-tool 集成)
├── ai_config.json         # AI API 配置 (.gitignore)
├── ai_sessions.json       # AI 对话 Session 持久化 (.gitignore)
├── all_keys.json          # 数据库密钥缓存 (.gitignore)
├── .gitignore             # 排除敏感文件
├── README.md              # 文档
├── decrypted/             # 解密后的数据库文件 (.gitignore)
├── templates/
│   ├── index.html         # 导出主页面
│   ├── setup.html         # 引导/解密页面
│   └── ai.html            # AI 分析页面
└── static/
    ├── style.css          # 样式
    └── app.js             # 前端逻辑
```

## 快速开始

### 前置条件

- macOS (Apple Silicon / Intel)
- Python 3.10+
- WeChat Mac 4.x 已安装并登录
- Flask: `pip3 install flask`

### 启动

```bash
cd ~/project/personal/wechat-extract-mac
sudo python3 app.py
```

打开浏览器访问：**http://127.0.0.1:9527**

> ⚠️ 需要 `sudo` 权限用于：重签名微信、LLDB 密钥捕获、解密数据库

### 首次使用流程（全部在网页完成）

1. **重签名微信** — 页面点击按钮或终端执行 `sudo codesign --force --deep --sign - /Applications/WeChat.app`
2. **重启微信** — 完全退出 (Cmd+Q) 再重新打开
3. **捕获密钥** — 点击页面按钮，按提示在微信中退出登录再重新登录
4. **解密数据库** — 点击按钮，等待进度完成后自动跳转

### 同步新消息

1. **退出微信** (Cmd+Q) — 让 WAL 数据合并到主数据库
2. **重新打开微信**
3. 在网页上点击 **「🔄 同步新消息」**

> 💡 微信使用 SQLite WAL 模式，最新消息先写入 .db-wal 文件。退出微信时 WAL 会自动合并。
> 本工具同时支持解密 WAL 文件，尽可能获取最新数据。

## 功能详解

### 聊天浏览

- **消息左右分开**：自己发的绿色靠右，对方白色靠左
- **显示发送者昵称**：使用备注名（非微信 ID），群聊中正确区分每个成员
- **头像显示**：对方消息左侧显示头像
- **跨数据库加载**：微信按时间分片存储（message_0~N.db），本工具自动合并所有分片
- **无限滚动**：往上滚动自动加载更老的消息
- **链接可点击**：公众号文章、新闻链接直接跳转
- **新闻聚合**：腾讯新闻等 mmreader 格式正确解析为多条链接

### 联系人列表

- 按最近活跃时间排序
- 显示消息总数（跨所有数据库分片累计）
- 标签区分：**Group**（群聊）/ **公众号**（gh_ 开头的官方账号）
- 支持搜索过滤

### 导出格式

- **HTML** — 微信风格聊天气泡，可在浏览器直接打开
- **TXT** — 纯文本，方便阅读和全文搜索
- **CSV** — 电子表格格式，方便数据分析

多选联系人后批量导出为 ZIP 包。

### AI 分析

#### 配置

右上角 ⚙️ 按钮设置：
- **API Base URL**: `https://api.moonshot.cn/v1` (Kimi)
- **API Key**: 从 https://platform.kimi.com 获取
- **Model**: `kimi-k3` (默认，支持推理和多模态)

也支持 OpenAI / Claude / DeepSeek 等所有 OpenAI 兼容 API。

#### 交互方式

1. **从导出页面进入** — 勾选联系人 → 点击「🤖 AI 分析」→ 自动带入聊天记录
2. **AI 页面内选择** — 顶部点击选择联系人/群组（支持多选）
3. **预设快捷提示** — 聊天频率、总结对话、情感分析、生成报告等

#### 功能特性

- **流式响应** — SSE 实时显示 AI 回复
- **深度思考** — 支持 Kimi K3 推理模式，可开关（🧠 深度思考）
- **中文推理** — 思考过程使用中文展示
- **多模态** — 支持上传图片（base64 内联）和文件（PDF/Excel/Word）
- **自动重试** — API 过载时自动重试最多 3 次
- **Markdown 渲染** — 标题、列表、表格、代码高亮
- **图表支持** — Mermaid 流程图、ECharts 数据可视化
- **Session 持久化** — 对话历史保存到文件，刷新/重启不丢失
- **多 Session** — 可在历史对话间切换，支持新建对话

### 图片支持

| 时间段 | 状态 | 说明 |
|--------|------|------|
| 2025 及之前 | ✅ 可显示 | 本地存储为纯 JPEG |
| 2026 起 | ❌ 加密 | V2 加密格式，暂无公开解密方法 |

### 解密技术原理

WeChat Mac 4.x 使用 SQLCipher 4 加密本地数据库：
- **加密算法**: AES-256-CBC
- **页大小**: 4096 bytes
- **KDF**: PBKDF2-HMAC-SHA512, 256000 迭代
- **HMAC**: SHA-512, 每页 64 bytes
- **Reserve**: 80 bytes (16 IV + 64 HMAC)

密钥提取方法：
1. 对微信 ad-hoc 重签名移除 Hardened Runtime
2. LLDB 在 `CCKeyDerivationPBKDF` 设断点
3. 用户退登再重登触发密钥派生
4. 从 ARM64 寄存器 `$x1` 读取 32 字节 passphrase
5. passphrase + 每个 DB 的 salt → PBKDF2 派生出 enc_key

WAL 解密：
- SQLCipher WAL 文件头为明文（标准 SQLite WAL magic `0x377f0682`）
- 帧头 24 bytes 为明文（含页号）
- 页数据 4096 bytes 使用与主 DB 相同的密钥加密
- 解密后创建配套 WAL 文件，SQLite 自动 checkpoint 合并

### 数据库分片

WeChat Mac 按时间将消息分散到多个数据库：
- `message_0.db` ~ `message_N.db` — 个人/群聊消息
- `biz_message_0.db` ~ `biz_message_N.db` — 公众号/服务号消息

本工具**动态发现所有分片**（不硬编码数量），跨分片合并消息并按时间排序。

## 端口

默认端口 **9527**（macOS 的 5000 端口被 AirPlay Receiver 占用）。

## 注意事项

- 重签名微信后部分系统权限（录屏等）需重新授权
- 每次微信自动更新后需重新签名
- passphrase 首次捕获后会缓存，后续同步无需重新捕获
- 同步前建议先退出微信再重开，确保 WAL 数据被写入主数据库
- 微信不会自动同步所有聊天到本地 — 需要打开对应聊天窗口才会写入

## 安全措施

本项目已通过安全审计并修复以下问题：

| 措施 | 说明 |
|------|------|
| 🔒 敏感文件保护 | `.gitignore` 排除 API Key、Session、密钥等文件 |
| 🛡️ Debug 关闭 | 生产模式不启用 Werkzeug 调试器（需 `FLASK_DEBUG=1` 显式开启） |
| 🚫 XSS 防御 | 微信消息中的 title/url 经 HTML 转义后再渲染为链接 |
| 🌐 SSRF 防御 | AI API Base URL 限制为 HTTPS + 已知 AI 域名白名单 |
| 📁 路径穿越防御 | 图片路由拒绝含 `..` 或路径分隔符的文件名 |
| 🔑 文件权限 | 敏感文件权限 `600`（仅所有者可读写） |
| 📦 上传限制 | 文件上传最大 16MB，防止内存耗尽 |
| 🏠 本地绑定 | 仅监听 `127.0.0.1`，外部网络无法访问 |

### AI API 域名白名单

配置 `api_base` 时仅允许以下域名：
- `api.moonshot.cn` (Kimi)
- `api.openai.com` (OpenAI)
- `api.anthropic.com` (Claude)
- `api.deepseek.com` (DeepSeek)
- `api.together.xyz` / `api.groq.com`
- `dashscope.aliyuncs.com` (阿里通义)
- `api.siliconflow.cn` / `api.lingyiwanwu.com` / `api.baichuan-ai.com` / `api.minimax.chat` / `api.zhipuai.cn`

如需添加其他 API 地址，修改 `app.py` 中的 `ALLOWED_HOSTS` 列表。

### 安全建议

- 如果 API Key 曾被提交到 git，请立即轮换
- 如需远程访问，建议添加 token 认证或 nginx 反代 + basic auth
- 定期清理 `ai_sessions.json` 中的历史对话数据
- 开发调试时可用 `FLASK_DEBUG=1 sudo python3 app.py` 启用调试模式

## 依赖

- Python 3.10+
- Flask
- pycryptodome (解密用，如不用内置 CommonCrypto)
- lldb (随 Xcode Command Line Tools 安装)

## License

仅供个人数据备份使用。请勿用于任何非法用途。

## Credits

- 解密引擎集成自 [wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)
- 灵感来自 [WeChatMsg](https://github.com/LC044/WeChatMsg)
