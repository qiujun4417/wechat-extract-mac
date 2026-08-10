# AI 配置指南

本项目支持两种 AI 接入方式：**OpenRouter（推荐）** 和 **自定义接口（兜底）**。

---

## 方式一：OpenRouter（推荐）

[OpenRouter](https://openrouter.ai) 是一个统一的 AI API 网关，一个 Key 即可访问 400+ 模型。

### 为什么推荐 OpenRouter？

| 优势 | 说明 |
|------|------|
| 🔑 一个 Key | 无需注册多个平台，一个 API Key 访问所有主流模型 |
| 🤖 400+ 模型 | GPT-4o, Claude, DeepSeek, Gemini, Llama, Mistral 等 |
| 💰 免费模型 | 25+ 个免费模型可用，适合测试和轻量使用 |
| 📊 统一计费 | 所有模型费用在一个面板查看，token 价格透明 |
| ⚡ 自动路由 | 内置 Provider 故障转移，可靠性更高 |

### 快速开始

1. 访问 [openrouter.ai](https://openrouter.ai) 注册账号
2. 进入 [Keys 页面](https://openrouter.ai/keys) 创建 API Key（格式：`sk-or-v1-...`）
3. 打开本工具的 AI 页面 → 点击 ⚙️ 设置
4. 在「OpenRouter」Tab 中粘贴 API Key
5. 点击「🔍 发现可用模型」→ 模型列表自动填充
6. 从顶部下拉框选择模型 → 开始对话

### 推荐模型

| 模型 | ID | 适用场景 |
|------|-----|---------|
| GPT-4o | `openai/gpt-4o` | 通用对话、代码、分析 |
| Claude Sonnet 4.6 | `anthropic/claude-sonnet-4-6` | 长文本、推理、写作 |
| DeepSeek V3 | `deepseek/deepseek-chat` | 性价比高、中文优秀 |
| DeepSeek Reasoner | `deepseek/deepseek-reasoner` | 深度推理（带思考过程） |
| Gemini 2.5 Pro | `google/gemini-2.5-pro-preview` | 多模态、长上下文 |
| Llama 3.3 70B | `meta-llama/llama-3.3-70b-instruct:free` | 免费、通用 |

### 模型 ID 格式

OpenRouter 的模型 ID 格式为 `{provider}/{model-name}`：
- `openai/gpt-4o`
- `anthropic/claude-sonnet-4-6`
- `deepseek/deepseek-chat`
- `google/gemini-2.5-pro-preview`
- `meta-llama/llama-3.3-70b-instruct`

带后缀的特殊变体：
- `:free` — 免费版（如 `meta-llama/llama-3.3-70b-instruct:free`）
- `:thinking` — 思考模式变体

### 费用说明

- 注册时有免费额度
- 充值时收取 5.5% 平台费，之后按 token 计费（价格与各平台直连相同）
- 25+ 个模型完全免费使用（有频率限制：20次/分钟）

---

## 方式二：自定义接口（兜底）

如果你已有特定平台的 API Key，或不想通过第三方中转，可直接配置：

### 配置方法

1. 打开 AI 页面 → ⚙️ 设置 → 切换到「自定义接口」Tab
2. 填写三个字段：

| 字段 | 示例 | 说明 |
|------|------|------|
| API Base URL | `https://api.moonshot.cn/v1` | 平台 API 地址 |
| API Key | `sk-...` | 对应平台的密钥 |
| Model | `kimi-k3` | 模型名称 |

### 支持的平台

| 平台 | API Base URL | 示例模型 |
|------|-------------|---------|
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | `kimi-k3`, `kimi-k2-0711-128k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat`, `deepseek-reasoner` |
| 阿里通义 | `https://dashscope.aliyuncs.com` | `qwen-max`, `qwen-plus` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | 各开源模型 |
| 智谱 AI | `https://api.zhipuai.cn/v1` | `glm-4`, `glm-4-flash` |

> 任何兼容 OpenAI `/v1/chat/completions` 接口格式的服务都可以接入。

### Kimi 特殊功能

直连 Kimi 时支持以下专属功能（OpenRouter 不支持）：
- **文件上传** — 上传 PDF/Excel/Word 文件，AI 直接分析文件内容
- **fileid:// 引用** — 通过文件 ID 引用已上传的文件

如果你需要文件上传功能，建议保留一个 Kimi 直连配置。

---

## 混合使用

你可以同时配置 OpenRouter 和自定义接口：
- 日常使用 OpenRouter（方便切换模型）
- 需要文件上传时切换到 Kimi 直连

顶部模型选择器会按 Provider 分组显示所有可用模型。

---

## 功能特性

所有配置方式都支持以下功能：

| 功能 | 说明 |
|------|------|
| 🔄 流式响应 | SSE 实时显示 AI 回复 |
| 🧠 深度思考 | 支持 thinking/reasoning 模式（DeepSeek、Claude 等） |
| 🖼️ 多模态 | 上传图片由 AI 分析（支持 Vision 的模型） |
| 📁 文件上传 | PDF/Excel/Word（仅 Kimi 直连） |
| 🔁 自动重试 | API 过载时自动重试最多 3 次 |
| 💾 Session 持久化 | 对话历史保存，刷新/重启不丢失 |

---

## 安全说明

### API Key 安全
- Key 保存在本地 `config/ai_config.json`，文件权限 `600`（仅所有者可读写）
- 前端展示时 Key 自动脱敏（仅显示首尾 4 位）
- `config/` 目录已添加到 `.gitignore`，不会提交到 git

### SSRF 防护
API Base URL 限制为 HTTPS 且仅允许已知域名：
- `openrouter.ai`、`api.openai.com`、`api.anthropic.com`
- `api.moonshot.cn`、`api.deepseek.com`、`api.together.xyz`
- 以及其他已配置 Provider 的域名

如需添加新域名，在设置中添加对应 Provider 即可自动加入白名单。

---

## 配置文件格式

配置保存在 `config/ai_config.json`：

```json
{
  "providers": [
    {
      "id": "openrouter",
      "name": "OpenRouter",
      "provider_type": "openrouter",
      "api_base": "https://openrouter.ai/api/v1",
      "api_key": "sk-or-v1-...",
      "models": [...],
      "capabilities": {"thinking": true, "vision": true, "file_upload": false},
      "auto_discover": true
    },
    {
      "id": "kimi",
      "name": "Kimi (Moonshot)",
      "provider_type": "openai_compatible",
      "api_base": "https://api.moonshot.cn/v1",
      "api_key": "sk-...",
      "models": ["kimi-k3"],
      "capabilities": {"thinking": true, "vision": true, "file_upload": true}
    }
  ],
  "active_model": "openai/gpt-4o",
  "active_provider": "openrouter"
}
```

> 从旧版本升级时，原有的 `{api_base, api_key, model}` 格式会在启动时自动迁移为新格式，无需手动操作。
