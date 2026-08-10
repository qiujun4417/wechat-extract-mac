"""AI Model Router — Multi-provider routing with OpenRouter + direct provider support.

Provides:
- Multi-provider config management (OpenRouter as primary, direct providers as fallback)
- Model discovery via /v1/models endpoint
- Unified SSE streaming with retry logic
- Legacy config migration
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Generator, Optional

import requests as req_lib


# ===== Error Codes =====

class AIError(Exception):
    """AI Router error with code and user-friendly message."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict:
        return {"error": self.message, "error_code": self.code}


ERROR_MESSAGES = {
    "PROVIDER_NOT_FOUND": "未找到指定的服务提供商「{provider_id}」，请先在设置中添加",
    "API_KEY_MISSING": "服务提供商「{provider_name}」未配置 API Key，请在设置中填写",
    "MODEL_NOT_SELECTED": "未选择模型，请在顶部下拉框选择一个模型或在设置中配置",
    "NO_PROVIDER_AVAILABLE": "未找到可用的服务提供商，请先在设置中配置 API Key",
    "SSRF_HTTPS_REQUIRED": "仅支持 HTTPS 协议的 API 地址",
    "SSRF_HOST_BLOCKED": "API 地址「{hostname}」不在允许列表中，如需添加请联系管理员",
    "DISCOVER_TIMEOUT": "查询可用模型超时，请检查网络连接后重试",
    "DISCOVER_CONN_ERROR": "无法连接到服务提供商，请检查网络连接",
    "DISCOVER_API_ERROR": "服务提供商返回错误（HTTP {status_code}），请检查 API Key 是否有效",
    "STREAM_TIMEOUT": "请求超时，已重试多次仍无法连接",
    "STREAM_CONN_ERROR": "连接错误：{detail}",
    "STREAM_API_ERROR": "API 返回错误：{detail}",
    "STREAM_UNEXPECTED": "未知错误：{detail}",
    "CONFIG_ACTION_UNKNOWN": "未知操作：{action}",
    "CONFIG_API_BASE_REQUIRED": "请填写 API Base URL",
    "CONFIG_PROVIDER_NOT_FOUND": "未找到指定的服务提供商",
}


# ===== Data Classes =====

@dataclass
class ProviderConfig:
    """Configuration for a single AI provider."""
    id: str
    name: str
    provider_type: str  # "openrouter", "openai_compatible"
    api_base: str
    api_key: str = ""
    models: list = field(default_factory=list)
    capabilities: dict = field(default_factory=lambda: {
        "thinking": False, "vision": False, "file_upload": False
    })
    auto_discover: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider_type": self.provider_type,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "models": self.models,
            "capabilities": self.capabilities,
            "auto_discover": self.auto_discover,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderConfig":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            provider_type=data.get("provider_type", "openai_compatible"),
            api_base=data.get("api_base", ""),
            api_key=data.get("api_key", ""),
            models=data.get("models", []),
            capabilities=data.get("capabilities", {
                "thinking": False, "vision": False, "file_upload": False
            }),
            auto_discover=data.get("auto_discover", False),
        )


@dataclass
class RouterConfig:
    """Full router configuration with multiple providers."""
    providers: list = field(default_factory=list)  # List[ProviderConfig]
    active_model: str = ""
    active_provider: str = ""

    def to_dict(self) -> dict:
        return {
            "providers": [p.to_dict() for p in self.providers],
            "active_model": self.active_model,
            "active_provider": self.active_provider,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RouterConfig":
        providers = [ProviderConfig.from_dict(p) for p in data.get("providers", [])]
        return cls(
            providers=providers,
            active_model=data.get("active_model", ""),
            active_provider=data.get("active_provider", ""),
        )


# ===== SSRF Protection =====

# Base allowed hosts (always permitted)
BASE_ALLOWED_HOSTS = [
    "api.moonshot.cn", "api.openai.com", "api.anthropic.com",
    "api.deepseek.com", "api.together.xyz", "api.groq.com",
    "generativelanguage.googleapis.com", "dashscope.aliyuncs.com",
    "api.siliconflow.cn", "api.lingyiwanwu.com", "api.baichuan-ai.com",
    "api.minimax.chat", "api.zhipuai.cn", "openrouter.ai",
]


def get_allowed_hosts(config: RouterConfig) -> list:
    """Get full allowed hosts list including configured provider domains."""
    from urllib.parse import urlparse
    hosts = list(BASE_ALLOWED_HOSTS)
    for provider in config.providers:
        if provider.api_base:
            parsed = urlparse(provider.api_base)
            if parsed.hostname and parsed.hostname not in hosts:
                hosts.append(parsed.hostname)
    return hosts


def validate_api_host(api_base: str, config: RouterConfig) -> Optional[dict]:
    """Validate that an API base URL is allowed. Returns error dict or None."""
    from urllib.parse import urlparse
    parsed = urlparse(api_base)
    if parsed.scheme != "https":
        return {"error": ERROR_MESSAGES["SSRF_HTTPS_REQUIRED"], "error_code": "SSRF_HTTPS_REQUIRED"}
    allowed = get_allowed_hosts(config)
    if not any(parsed.hostname == h or (parsed.hostname and parsed.hostname.endswith("." + h)) for h in allowed):
        msg = ERROR_MESSAGES["SSRF_HOST_BLOCKED"].format(hostname=parsed.hostname)
        return {"error": msg, "error_code": "SSRF_HOST_BLOCKED"}
    return None


# ===== Model Router =====

class ModelRouter:
    """Routes AI requests to the correct provider based on model selection."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> RouterConfig:
        """Load config from file, auto-migrating legacy format if needed."""
        if not os.path.exists(self.config_path):
            return self._create_default_config()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return self._create_default_config()

        # Detect legacy format (has api_base at root level, no providers array)
        if "api_base" in data and "providers" not in data:
            config = migrate_legacy_config(data)
            self._save_config(config)
            return config

        return RouterConfig.from_dict(data)

    def _create_default_config(self) -> RouterConfig:
        """Create default config with empty OpenRouter provider."""
        config = RouterConfig(
            providers=[
                ProviderConfig(
                    id="openrouter",
                    name="OpenRouter",
                    provider_type="openrouter",
                    api_base="https://openrouter.ai/api/v1",
                    api_key="",
                    models=[],
                    capabilities={"thinking": True, "vision": True, "file_upload": False},
                    auto_discover=True,
                )
            ],
            active_model="",
            active_provider="openrouter",
        )
        self._save_config(config)
        return config

    def _save_config(self, config: Optional[RouterConfig] = None):
        """Save current config to file."""
        if config is None:
            config = self.config
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
        # Secure file permissions
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass

    def save(self):
        """Public method to persist current config."""
        self._save_config()

    def reload(self):
        """Reload config from disk."""
        self.config = self._load_config()

    # ----- Provider Management -----

    def get_provider(self, provider_id: str) -> Optional[ProviderConfig]:
        """Get a provider by ID."""
        for p in self.config.providers:
            if p.id == provider_id:
                return p
        return None

    def add_provider(self, provider: ProviderConfig):
        """Add a new provider."""
        # Remove existing with same id
        self.config.providers = [p for p in self.config.providers if p.id != provider.id]
        self.config.providers.append(provider)
        self._save_config()

    def remove_provider(self, provider_id: str):
        """Remove a provider by ID."""
        self.config.providers = [p for p in self.config.providers if p.id != provider_id]
        if self.config.active_provider == provider_id:
            # Switch to first available
            if self.config.providers:
                self.config.active_provider = self.config.providers[0].id
            else:
                self.config.active_provider = ""
                self.config.active_model = ""
        self._save_config()

    def update_provider(self, provider_id: str, updates: dict):
        """Update specific fields of a provider."""
        provider = self.get_provider(provider_id)
        if not provider:
            return False
        for key, value in updates.items():
            if hasattr(provider, key):
                setattr(provider, key, value)
        self._save_config()
        return True

    # ----- Model Routing -----

    def get_available_models(self) -> list:
        """Return flat list of all available models with provider info."""
        models = []
        for provider in self.config.providers:
            if not provider.api_key:
                continue
            for model in provider.models:
                model_id = model if isinstance(model, str) else model.get("id", "")
                model_name = model if isinstance(model, str) else model.get("name", model_id)
                models.append({
                    "id": model_id,
                    "name": model_name,
                    "provider": provider.id,
                    "provider_name": provider.name,
                    "capabilities": provider.capabilities,
                })
        return models

    def get_provider_for_model(self, model_id: str) -> Optional[ProviderConfig]:
        """Find which provider owns a given model ID."""
        if not model_id:
            return self._get_active_provider()

        for provider in self.config.providers:
            model_ids = [
                m if isinstance(m, str) else m.get("id", "")
                for m in provider.models
            ]
            if model_id in model_ids:
                return provider

        # For OpenRouter, any model with "/" in name routes there
        if "/" in model_id:
            for provider in self.config.providers:
                if provider.provider_type == "openrouter" and provider.api_key:
                    return provider

        # Fallback to active provider
        return self._get_active_provider()

    def _get_active_provider(self) -> Optional[ProviderConfig]:
        """Get the currently active provider."""
        if self.config.active_provider:
            provider = self.get_provider(self.config.active_provider)
            if provider and provider.api_key:
                return provider
        # Fallback: first provider with a key
        for p in self.config.providers:
            if p.api_key:
                return p
        return None

    def get_active_model(self) -> str:
        """Get the currently active model ID."""
        if self.config.active_model:
            return self.config.active_model
        # Fallback: first model of active provider
        provider = self._get_active_provider()
        if provider and provider.models:
            m = provider.models[0]
            return m if isinstance(m, str) else m.get("id", "")
        return ""

    def set_active_model(self, model_id: str):
        """Set the active model and update active_provider accordingly."""
        self.config.active_model = model_id
        provider = self.get_provider_for_model(model_id)
        if provider:
            self.config.active_provider = provider.id
        self._save_config()

    # ----- Model Discovery -----

    def discover_models(self, provider_id: str) -> dict:
        """Query a provider's /v1/models endpoint to discover available models.
        Returns {"models": [...], "error": None} or {"models": [], "error": "...", "error_code": "..."}
        """
        provider = self.get_provider(provider_id)
        if not provider:
            return {"models": [], "error": ERROR_MESSAGES["PROVIDER_NOT_FOUND"].format(provider_id=provider_id), "error_code": "PROVIDER_NOT_FOUND"}
        if not provider.api_key:
            return {"models": [], "error": ERROR_MESSAGES["API_KEY_MISSING"].format(provider_name=provider.name), "error_code": "API_KEY_MISSING"}

        try:
            headers = {"Authorization": f"Bearer {provider.api_key}"}
            if provider.provider_type == "openrouter":
                headers["HTTP-Referer"] = "https://wechat-extract-mac.local"
                headers["X-OpenRouter-Title"] = "WeChat Extract AI"

            resp = req_lib.get(
                f"{provider.api_base.rstrip('/')}/models",
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                return {"models": [], "error": ERROR_MESSAGES["DISCOVER_API_ERROR"].format(status_code=resp.status_code), "error_code": "DISCOVER_API_ERROR"}

            data = resp.json()
            raw_models = data.get("data", [])

            # Parse model list
            discovered = []
            for m in raw_models:
                model_id = m.get("id", "")
                if not model_id:
                    continue
                model_info = {
                    "id": model_id,
                    "name": m.get("name", model_id),
                }
                # OpenRouter provides extra metadata
                if provider.provider_type == "openrouter":
                    arch = m.get("architecture", {})
                    pricing = m.get("pricing", {})
                    model_info["context_length"] = m.get("context_length", 0)
                    model_info["input_modalities"] = arch.get("input_modalities", ["text"])
                    model_info["pricing_prompt"] = pricing.get("prompt", "0")
                    model_info["pricing_completion"] = pricing.get("completion", "0")
                discovered.append(model_info)

            # Update provider's model list
            provider.models = discovered
            self._save_config()

            return {"models": discovered, "error": None}

        except req_lib.exceptions.Timeout:
            return {"models": [], "error": ERROR_MESSAGES["DISCOVER_TIMEOUT"], "error_code": "DISCOVER_TIMEOUT"}
        except req_lib.exceptions.ConnectionError:
            return {"models": [], "error": ERROR_MESSAGES["DISCOVER_CONN_ERROR"], "error_code": "DISCOVER_CONN_ERROR"}
        except Exception as e:
            return {"models": [], "error": str(e)}

    # ----- Request Building -----

    def build_request_params(self, model_id: str, messages: list,
                             thinking: Optional[bool] = None) -> tuple:
        """Build (url, headers, payload) for a streaming chat completion request.

        Returns:
            tuple: (api_url, headers_dict, payload_dict)
        Raises:
            AIError: if no valid provider found
        """
        provider = self.get_provider_for_model(model_id)
        if not provider:
            raise AIError("NO_PROVIDER_AVAILABLE", ERROR_MESSAGES["NO_PROVIDER_AVAILABLE"])
        if not provider.api_key:
            raise AIError("API_KEY_MISSING", ERROR_MESSAGES["API_KEY_MISSING"].format(provider_name=provider.name))

        # Resolve model_id: if empty, use active model or provider's first model
        if not model_id:
            model_id = self.get_active_model()
        if not model_id:
            raise AIError("MODEL_NOT_SELECTED", ERROR_MESSAGES["MODEL_NOT_SELECTED"])

        api_url = f"{provider.api_base.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        # OpenRouter-specific headers
        if provider.provider_type == "openrouter":
            headers["HTTP-Referer"] = "https://wechat-extract-mac.local"
            headers["X-OpenRouter-Title"] = "WeChat Extract AI"

        payload = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }

        # Handle thinking parameter
        if thinking is False:
            payload["thinking"] = {"type": "disabled"}
        elif thinking is True:
            payload["thinking"] = {"type": "enabled", "budget_tokens": 4096}
        # If None/"auto", don't add thinking param

        return api_url, headers, payload


# ===== Unified SSE Streaming =====

def stream_chat_sse(url: str, headers: dict, payload: dict,
                    max_retries: int = 3, timeout: int = 120) -> Generator[str, None, None]:
    """Unified streaming generator: calls API and yields SSE event strings.

    Handles:
    - Streaming SSE parsing (data: lines)
    - Thinking/reasoning_content buffering
    - Retry on 429/503/timeout
    - Error reporting

    Yields:
        str: SSE event lines like 'data: {"content": "..."}\n\n'
    """
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            resp = req_lib.post(url, json=payload, headers=headers,
                                stream=True, timeout=timeout)

            if resp.status_code != 200:
                error_msg = f"API returned status {resp.status_code}"
                try:
                    error_body = resp.json()
                    if "error" in error_body:
                        error_msg = error_body["error"].get("message", error_msg)
                except Exception:
                    pass

                # Retry on overload/rate limit
                is_retryable = (
                    resp.status_code in (429, 503) or
                    "overload" in error_msg.lower() or
                    "rate" in error_msg.lower() or
                    "try again" in error_msg.lower()
                )
                if is_retryable and attempt < max_retries - 1:
                    wait = retry_delay * (attempt + 1)
                    retry_msg = f"\n\n⏳ 服务繁忙，{wait}秒后自动重试 ({attempt+1}/{max_retries})...\n\n"
                    yield _sse_event({"content": retry_msg})
                    time.sleep(wait)
                    continue

                yield _sse_event({"error": ERROR_MESSAGES["STREAM_API_ERROR"].format(detail=error_msg)})
                yield _sse_event({"done": True})
                return

            # Success — stream response
            thinking_buffer = ""
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace")

                # Skip OpenRouter keep-alive comments
                if line.startswith(":"):
                    continue

                if line.startswith("data: "):
                    payload_str = line[6:]
                    if payload_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            reasoning = delta.get("reasoning_content")

                            if content:
                                # Flush thinking buffer before content
                                if thinking_buffer:
                                    yield _sse_event({"thinking": thinking_buffer})
                                    thinking_buffer = ""
                                yield _sse_event({"content": content})
                            elif reasoning:
                                # Buffer thinking output, flush at >= 20 chars
                                thinking_buffer += reasoning
                                if len(thinking_buffer) >= 20:
                                    yield _sse_event({"thinking": thinking_buffer})
                                    thinking_buffer = ""
                    except json.JSONDecodeError:
                        continue

            # Flush remaining thinking buffer
            if thinking_buffer:
                yield _sse_event({"thinking": thinking_buffer})

            yield _sse_event({"done": True})
            return  # Success, exit retry loop

        except req_lib.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = retry_delay * (attempt + 1)
                yield _sse_event({"content": f"\n\n⏳ 请求超时，{wait}秒后重试 ({attempt+1}/{max_retries})...\n\n"})
                time.sleep(wait)
                continue
            yield _sse_event({"error": ERROR_MESSAGES["STREAM_TIMEOUT"]})
            yield _sse_event({"done": True})

        except req_lib.exceptions.ConnectionError as e:
            if attempt < max_retries - 1:
                wait = retry_delay * (attempt + 1)
                yield _sse_event({"content": f"\n\n⏳ 连接错误，{wait}秒后重试...\n\n"})
                time.sleep(wait)
                continue
            yield _sse_event({"error": ERROR_MESSAGES["STREAM_CONN_ERROR"].format(detail=str(e))})
            yield _sse_event({"done": True})

        except Exception as e:
            yield _sse_event({"error": ERROR_MESSAGES["STREAM_UNEXPECTED"].format(detail=str(e))})
            yield _sse_event({"done": True})
            return


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ===== Config Migration =====

def migrate_legacy_config(old_config: dict) -> RouterConfig:
    """Migrate legacy single-provider config to new multi-provider format.

    Legacy format: {"api_base": "...", "api_key": "...", "model": "..."}
    """
    api_base = old_config.get("api_base", "https://api.moonshot.cn/v1")
    api_key = old_config.get("api_key", "")
    model = old_config.get("model", "")

    # Determine provider type and ID from api_base
    from urllib.parse import urlparse
    parsed = urlparse(api_base)
    hostname = parsed.hostname or ""

    if "openrouter.ai" in hostname:
        provider_id = "openrouter"
        provider_name = "OpenRouter"
        provider_type = "openrouter"
        capabilities = {"thinking": True, "vision": True, "file_upload": False}
        auto_discover = True
    elif "moonshot.cn" in hostname:
        provider_id = "kimi"
        provider_name = "Kimi (Moonshot)"
        provider_type = "openai_compatible"
        capabilities = {"thinking": True, "vision": True, "file_upload": True}
        auto_discover = False
    elif "deepseek.com" in hostname:
        provider_id = "deepseek"
        provider_name = "DeepSeek"
        provider_type = "openai_compatible"
        capabilities = {"thinking": True, "vision": False, "file_upload": False}
        auto_discover = False
    elif "openai.com" in hostname:
        provider_id = "openai"
        provider_name = "OpenAI"
        provider_type = "openai_compatible"
        capabilities = {"thinking": False, "vision": True, "file_upload": False}
        auto_discover = False
    else:
        provider_id = "custom"
        provider_name = hostname or "Custom"
        provider_type = "openai_compatible"
        capabilities = {"thinking": False, "vision": False, "file_upload": False}
        auto_discover = False

    models = [model] if model else []

    provider = ProviderConfig(
        id=provider_id,
        name=provider_name,
        provider_type=provider_type,
        api_base=api_base,
        api_key=api_key,
        models=models,
        capabilities=capabilities,
        auto_discover=auto_discover,
    )

    return RouterConfig(
        providers=[provider],
        active_model=model,
        active_provider=provider_id,
    )


# ===== Convenience: Config API helpers =====

def get_config_for_api(router: ModelRouter) -> dict:
    """Get config data formatted for the frontend API response (masks keys)."""
    config = router.config
    providers_masked = []
    for p in config.providers:
        masked = p.to_dict()
        if masked.get("api_key"):
            key = masked["api_key"]
            if len(key) > 8:
                masked["api_key"] = key[:4] + "*" * (len(key) - 8) + key[-4:]
            else:
                masked["api_key"] = "****"
        providers_masked.append(masked)

    return {
        "providers": providers_masked,
        "active_model": config.active_model,
        "active_provider": config.active_provider,
        "models": router.get_available_models(),
    }
