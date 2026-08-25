"""User-Defined Custom Tools — let users extend the agent without writing code.

Tools are defined by name + description + parameter schema + prompt template.
At runtime, the tool:
1. Loads relevant chat messages (if username param is provided)
2. Fills the prompt template with parameters and message content
3. Makes a non-streaming LLM call
4. Returns the result

Persisted in config/custom_tools.json.
"""

import json
import os
import threading
import time
import uuid

import requests

_TOOLS_FILE = None
_tools_cache = []  # list of tool dicts
_lock = threading.Lock()


def init(config_dir: str):
    global _TOOLS_FILE
    _TOOLS_FILE = os.path.join(config_dir, "custom_tools.json")
    _load()


def _load():
    global _tools_cache
    if not _TOOLS_FILE or not os.path.exists(_TOOLS_FILE):
        return
    try:
        with open(_TOOLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _tools_cache = data.get("tools", [])
    except Exception:
        pass


def _save():
    if not _TOOLS_FILE:
        return
    try:
        os.makedirs(os.path.dirname(_TOOLS_FILE), exist_ok=True)
        with _lock:
            data = {"tools": list(_tools_cache)}
        with open(_TOOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[CustomTools] Save error: {e}")


def list_tools() -> list:
    with _lock:
        return list(_tools_cache)


def get_tool(tool_id: str) -> dict:
    with _lock:
        return next((t for t in _tools_cache if t["id"] == tool_id), None)


def create_tool(name: str, description: str, parameters: dict, prompt_template: str) -> dict:
    tool = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "description": description,
        "parameters": parameters or {},
        "prompt_template": prompt_template,
        "created_at": time.time(),
    }
    with _lock:
        _tools_cache.append(tool)
    _save()
    return tool


def delete_tool(tool_id: str) -> bool:
    with _lock:
        before = len(_tools_cache)
        _tools_cache[:] = [t for t in _tools_cache if t["id"] != tool_id]
        changed = len(_tools_cache) < before
    if changed:
        _save()
    return changed


def register_in_registry(registry, model_router):
    """Register all custom tools into a ToolRegistry instance."""
    tools = list_tools()
    for tool in tools:
        _register_single(registry, tool, model_router)


def _register_single(registry, tool: dict, model_router):
    """Register one custom tool as a callable in the registry."""
    name = tool["name"]
    description = tool["description"]
    params = tool.get("parameters", {})
    prompt_template = tool.get("prompt_template", "")

    input_schema = {
        "type": "object",
        "properties": params,
        "required": [k for k, v in params.items() if not isinstance(v.get("default"), type(None))],
    }

    def tool_fn(**kwargs):
        # Build prompt — inject messages if username param provided
        messages_text = ""
        username = kwargs.get("username")
        if username:
            try:
                import app as wechat_app
                days = int(kwargs.get("days", 7))
                cutoff = time.time() - days * 86400
                msgs = wechat_app.get_messages_by_timerange(username, time_from=int(cutoff), limit=100)
                lines = []
                for m in msgs:
                    sender = "我" if m["is_self"] else (m.get("sender_name") or username)
                    lines.append(f"[{m['time_str']}] {sender}: {m['content']}")
                messages_text = "\n".join(lines)
            except Exception as e:
                messages_text = f"（无法加载消息: {e}）"

        # Fill template
        fill_kwargs = dict(kwargs)
        fill_kwargs["messages"] = messages_text
        try:
            prompt = prompt_template.format(**fill_kwargs)
        except KeyError as e:
            prompt = prompt_template + f"\n\n参数: {json.dumps(kwargs, ensure_ascii=False)}"

        # Call LLM
        try:
            provider = model_router._get_active_provider()
            if not provider or not provider.api_key:
                return {"error": "未配置 AI 服务"}
            api_url, headers, _ = model_router.build_request_params(
                model_router.get_active_model(), [], thinking=None
            )
            payload = {
                "model": model_router.get_active_model(),
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 500,
            }
            resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"result": result}
            return {"error": f"LLM 调用失败 ({resp.status_code})"}
        except Exception as e:
            return {"error": str(e)}

    registry.register(name, description, input_schema, tool_fn)
