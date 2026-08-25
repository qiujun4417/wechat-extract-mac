"""AI Agent — Tool registry, streaming tool-call accumulator, and agent loop.

Inspired by DeepSeek Harness (dsh) plugin architecture:
- ToolRegistry: register/serialize/execute tools (like dsh ctx.tools)
- stream_and_collect_tool_calls: accumulate delta.tool_calls chunks from SSE
- run_agent_loop: LLM → tool_calls → execute → loop until final answer
"""

import json
import threading
import time
import requests
from typing import Generator


# ───────────────────────────── ToolRegistry ─────────────────────────────


class ToolRegistry:
    """Plugin-style tool registry. Register Python functions as LLM tools."""

    def __init__(self):
        self._tools = {}  # name -> {"description": str, "input_schema": dict, "fn": callable, ...}

    def register(self, name: str, description: str, input_schema: dict, fn):
        """Register a tool. input_schema is JSON Schema for the parameters object."""
        self._tools[name] = {
            "description": description,
            "input_schema": input_schema,
            "fn": fn,
            "requires_approval": False,
        }

    def mark_requires_approval(self, name: str):
        """Mark a tool as requiring user approval before execution."""
        if name in self._tools:
            self._tools[name]["requires_approval"] = True

    def to_openai_tools(self) -> list:
        """Serialize registry to OpenAI function-calling tools format."""
        result = []
        for name, info in self._tools.items():
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["input_schema"],
                },
            })
        return result

    def execute(self, name: str, arguments: dict) -> str:
        """Execute a tool by name. Returns JSON string of the result."""
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        try:
            result = tool["fn"](**arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except TypeError as e:
            return json.dumps({"error": f"工具参数错误: {e}"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)

    def has(self, name: str) -> bool:
        return name in self._tools


# ──────────────────── Default registry factory ────────────────────────


def make_default_registry(context_usernames: list) -> ToolRegistry:
    """Create the default WeChat data tool registry for AI chat sessions.

    Imports app data functions lazily to avoid circular imports.
    context_usernames: list of usernames currently in scope for the session.
    """
    import app as wechat_app

    registry = ToolRegistry()

    # ── search_messages ──
    registry.register(
        name="search_messages",
        description=(
            "在指定联系人的聊天记录中全文搜索关键词。"
            "返回匹配的消息列表，包含时间、发送者和内容。"
            "注意：压缩类型的消息可能不会被搜索到。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": f"联系人的微信用户名（wxid），可选值: {context_usernames}",
                },
                "keyword": {
                    "type": "string",
                    "description": "要搜索的关键词",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回的消息条数，默认 20，最大 50",
                    "default": 20,
                },
            },
            "required": ["username", "keyword"],
        },
        fn=lambda username, keyword, limit=20: wechat_app.search_messages(
            username, keyword, min(int(limit), 50)
        ),
    )

    # ── get_messages_in_timerange ──
    registry.register(
        name="get_messages_in_timerange",
        description=(
            "获取指定联系人在某个时间范围内的聊天记录。"
            "time_from 和 time_to 均为 Unix 时间戳（秒）。"
            "不传时间则返回最近消息。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": f"联系人的微信用户名，可选值: {context_usernames}",
                },
                "time_from": {
                    "type": "integer",
                    "description": "开始时间戳（秒），0 表示不限",
                    "default": 0,
                },
                "time_to": {
                    "type": "integer",
                    "description": "结束时间戳（秒），0 表示不限",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 100，最大 200",
                    "default": 100,
                },
            },
            "required": ["username"],
        },
        fn=lambda username, time_from=0, time_to=0, limit=100: wechat_app.get_messages_by_timerange(
            username, int(time_from), int(time_to), min(int(limit), 200)
        ),
    )

    # ── get_contact_stats ──
    registry.register(
        name="get_contact_stats",
        description=(
            "获取指定联系人的聊天统计信息：总消息数、最早/最新消息时间。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": f"联系人的微信用户名，可选值: {context_usernames}",
                },
            },
            "required": ["username"],
        },
        fn=lambda username: _get_contact_stats(username),
    )

    # ── get_contacts_list ──
    registry.register(
        name="get_contacts_list",
        description=(
            "获取微信联系人列表（包含 username、显示名称、是否群聊等信息）。"
            "可用于查找特定联系人的 username 以供其他工具使用。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "include_groups": {
                    "type": "boolean",
                    "description": "是否包含群聊，默认 true",
                    "default": True,
                },
                "include_official": {
                    "type": "boolean",
                    "description": "是否包含公众号，默认 false",
                    "default": False,
                },
                "keyword": {
                    "type": "string",
                    "description": "按名称过滤（可选）",
                    "default": "",
                },
            },
        },
        fn=lambda include_groups=True, include_official=False, keyword="": _get_contacts_list(
            include_groups, include_official, keyword
        ),
    )

    # ── semantic_search (optional — only registered when chromadb is available) ──
    try:
        import ai_semantic as _sem
        if _sem.is_available():
            registry.register(
                name="semantic_search",
                description=(
                    "对指定联系人的聊天记录进行语义相似度搜索（比 search_messages 更智能，"
                    "搜'项目'能找到'工程'、'任务'等语义相关内容）。"
                    "需要先建立索引才能使用。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": f"联系人的微信用户名，可选值: {context_usernames}",
                        },
                        "query": {
                            "type": "string",
                            "description": "语义搜索查询（自然语言描述）",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "最多返回条数，默认 20",
                            "default": 20,
                        },
                    },
                    "required": ["username", "query"],
                },
                fn=lambda username, query, limit=20: _sem.search(username, query, int(limit)),
            )
    except Exception:
        pass

    return registry


def _get_contact_stats(username: str) -> dict:
    import app as wechat_app
    count = wechat_app.get_message_count(username)
    last_time = wechat_app.get_last_message_time(username)
    # Get first message time via a direct query
    from app import find_message_table, get_db, md5_hash
    first_time = 0
    tables = find_message_table(username)
    for db_path, table_name in tables:
        try:
            conn = get_db(db_path)
            cursor = conn.execute(
                f"SELECT MIN(create_time) as ft FROM [{table_name}] WHERE create_time > 0"
            )
            row = cursor.fetchone()
            conn.close()
            if row and row["ft"] and (first_time == 0 or row["ft"] < first_time):
                first_time = row["ft"]
        except Exception:
            continue

    from datetime import datetime
    def ts_to_str(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""

    return {
        "username": username,
        "message_count": count,
        "first_message_time": first_time,
        "first_message_date": ts_to_str(first_time),
        "last_message_time": last_time,
        "last_message_date": ts_to_str(last_time),
    }


def _get_contacts_list(include_groups: bool, include_official: bool, keyword: str) -> list:
    import app as wechat_app
    contacts = wechat_app.get_all_contacts()
    result = []
    kw = keyword.lower() if keyword else ""
    for c in contacts:
        if not include_groups and c["is_group"]:
            continue
        if not include_official and c["is_official"]:
            continue
        display = c.get("remark") or c.get("nick_name") or c["username"]
        if kw and kw not in display.lower() and kw not in c["username"].lower():
            continue
        result.append({
            "username": c["username"],
            "display_name": display,
            "is_group": c["is_group"],
            "is_official": c["is_official"],
        })
    return result[:200]


# ──────────────── stream_and_collect_tool_calls ────────────────────────


def stream_and_collect_tool_calls(
    url: str,
    headers: dict,
    payload: dict,
    timeout: int = 120,
) -> tuple:
    """Stream an LLM response and collect tool_calls chunks.

    Returns (content, tool_calls, thinking, error):
      content   str  — final text content (may be empty if tool_calls present)
      tool_calls list — [{id, name, arguments: dict}] or []
      thinking  str  — accumulated reasoning_content
      error     str or None
    """
    content = ""
    thinking = ""
    error = None
    tool_calls_raw = {}  # {index: {id, name, arguments_str}}

    try:
        resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=timeout)

        if resp.status_code != 200:
            error_msg = f"API 返回 {resp.status_code}"
            try:
                body = resp.json()
                if "error" in body:
                    error_msg = body["error"].get("message", error_msg)
            except Exception:
                pass
            return content, [], thinking, error_msg

        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace")
            if line.startswith(":"):
                continue
            if not line.startswith("data: "):
                continue
            payload_str = line[6:]
            if payload_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            # Accumulate content
            if delta.get("content"):
                content += delta["content"]

            # Accumulate reasoning/thinking
            if delta.get("reasoning_content"):
                thinking += delta["reasoning_content"]

            # Accumulate tool_calls chunks
            for tc_chunk in delta.get("tool_calls", []):
                idx = tc_chunk.get("index", 0)
                if idx not in tool_calls_raw:
                    tool_calls_raw[idx] = {"id": "", "name": "", "arguments_str": ""}
                tc = tool_calls_raw[idx]
                if tc_chunk.get("id"):
                    tc["id"] = tc_chunk["id"]
                fn = tc_chunk.get("function", {})
                if fn.get("name"):
                    tc["name"] = fn["name"]
                if fn.get("arguments"):
                    tc["arguments_str"] += fn["arguments"]

    except requests.exceptions.Timeout:
        return content, [], thinking, "请求超时，请重试"
    except requests.exceptions.ConnectionError as e:
        return content, [], thinking, f"连接错误: {e}"
    except Exception as e:
        return content, [], thinking, f"未知错误: {e}"

    # Parse accumulated argument strings
    tool_calls = []
    for idx in sorted(tool_calls_raw.keys()):
        tc = tool_calls_raw[idx]
        try:
            arguments = json.loads(tc["arguments_str"]) if tc["arguments_str"] else {}
        except json.JSONDecodeError:
            arguments = {"_raw": tc["arguments_str"]}
        tool_calls.append({
            "id": tc["id"],
            "name": tc["name"],
            "arguments": arguments,
        })

    return content, tool_calls, thinking, error


# ─────────────────────────── run_agent_loop ───────────────────────────


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_agent_loop(
    api_url: str,
    headers: dict,
    base_payload: dict,
    messages: list,
    tools: ToolRegistry,
    max_iterations: int = 10,
) -> Generator[str, None, None]:
    """Agent loop generator — yields SSE strings.

    Adds tools to payload, loops until LLM produces a final content response
    (no tool_calls) or max_iterations is reached.

    New SSE event types yielded:
      {"tool_call": {"name": str, "args": dict}}   — before tool execution
      {"tool_result": {"name": str, "result": str}} — after tool execution
      {"content": str}                               — final answer
      {"thinking": str}                              — reasoning content
      {"error": str}                                 — on failure
      {"done": true}                                 — always last
    """
    loop_messages = list(messages)

    # Inject tools into payload
    payload = dict(base_payload)
    payload["tools"] = tools.to_openai_tools()
    payload["tool_choice"] = "auto"
    # Remove thinking param when using tools (some providers conflict)
    payload.pop("thinking", None)

    for iteration in range(max_iterations):
        payload["messages"] = loop_messages

        content, tool_calls, thinking, error = stream_and_collect_tool_calls(
            api_url, headers, payload
        )

        if error:
            yield _sse({"error": error})
            yield _sse({"done": True})
            return

        if thinking:
            yield _sse({"thinking": thinking})

        if not tool_calls:
            # Final answer — no more tool calls
            if content:
                yield _sse({"content": content})
            yield _sse({"done": True})
            return

        # Append assistant message with tool_calls
        assistant_msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }
        loop_messages.append(assistant_msg)

        # Execute each tool, yield progress events, append results
        for tc in tool_calls:
            # Check if tool requires user approval
            tool_info = tools._tools.get(tc["name"], {})
            if tool_info.get("requires_approval"):
                approval_event = threading.Event()
                approval_result = [True]  # default: auto-approve after timeout
                # Register in global pending approvals (imported from app at runtime)
                try:
                    import app as _app
                    _app._pending_approvals[tc["id"]] = (approval_event, approval_result)
                except Exception:
                    pass
                yield _sse({"tool_approval_needed": {
                    "id": tc["id"],
                    "name": tc["name"],
                    "args": tc["arguments"],
                }})
                # Wait up to 30s for user decision (auto-approve on timeout)
                approval_event.wait(timeout=30)
                if not approval_result[0]:
                    yield _sse({"tool_result": {"name": tc["name"], "result": "用户已拒绝此工具调用"}})
                    loop_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "用户已拒绝此工具调用",
                    })
                    continue

            yield _sse({"tool_call": {"name": tc["name"], "args": tc["arguments"]}})
            result_str = tools.execute(tc["name"], tc["arguments"])
            # Cap large results to avoid ballooning context
            if len(result_str) > 8000:
                result_str = result_str[:8000] + "... (truncated)"
            yield _sse({"tool_result": {"name": tc["name"], "result": result_str}})
            loop_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            })

    yield _sse({"error": f"Agent 已执行 {max_iterations} 轮仍未得出最终答案"})
    yield _sse({"done": True})


# ──────────────────── run_plan_and_execute_loop ───────────────────────


def run_plan_and_execute_loop(
    api_url: str,
    headers: dict,
    base_payload: dict,
    messages: list,
    tools: ToolRegistry,
    max_iterations: int = 10,
) -> Generator[str, None, None]:
    """Plan-and-Execute agent loop.

    Step 1: Ask the model to produce a numbered plan (no tools available).
    Step 2: Execute the plan using run_agent_loop with tools.

    New SSE event type yielded:
      {"plan_start": {"steps": [str, ...]}}  — before execution begins
    """
    # Planning turn — no tools, just ask for a plan
    plan_payload = {k: v for k, v in base_payload.items() if k != "tools"}
    plan_payload.pop("tool_choice", None)
    plan_messages = list(messages) + [{
        "role": "system",
        "content": (
            "在正式回答之前，先输出一个执行计划，格式严格如下：\n"
            "PLAN:\n1. 第一步\n2. 第二步\n3. 第三步\nEND_PLAN\n\n"
            "然后再开始执行和回答。计划要具体，每步不超过20字。"
        ),
    }]
    plan_payload["messages"] = plan_messages

    plan_content, _, _, plan_error = stream_and_collect_tool_calls(
        api_url, headers, plan_payload
    )

    if plan_error:
        # Fall back to normal agent loop on planning failure
        yield from run_agent_loop(api_url, headers, base_payload, messages, tools, max_iterations)
        return

    # Parse plan steps
    steps = []
    if "PLAN:" in plan_content and "END_PLAN" in plan_content:
        raw_plan = plan_content.split("PLAN:")[1].split("END_PLAN")[0].strip()
        for line in raw_plan.split("\n"):
            line = line.strip()
            if line:
                # Strip leading "1. " "2. " etc.
                import re
                clean = re.sub(r"^\d+[\.\)]\s*", "", line)
                if clean:
                    steps.append(clean)

    if steps:
        yield _sse({"plan_start": {"steps": steps}})

    # Execution turn — inject plan as context, run with tools
    messages_with_plan = list(messages) + [
        {"role": "assistant", "content": plan_content}
    ]
    yield from run_agent_loop(
        api_url, headers, base_payload, messages_with_plan, tools, max_iterations
    )

    yield _sse({"error": f"Agent 已执行 {max_iterations} 轮仍未得出最终答案"})
    yield _sse({"done": True})
