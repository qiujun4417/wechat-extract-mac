# Changelog

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
