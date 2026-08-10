"""Tests for ai_router.py — Multi-provider model routing."""

import json
import os
import tempfile
import pytest

from ai_router import (
    AIError,
    ModelRouter,
    ProviderConfig,
    RouterConfig,
    migrate_legacy_config,
    stream_chat_sse,
    get_config_for_api,
    validate_api_host,
    BASE_ALLOWED_HOSTS,
    _sse_event,
)


# ===== Fixtures =====

@pytest.fixture
def tmp_config(tmp_path):
    """Temporary config file path."""
    return str(tmp_path / "ai_config.json")


@pytest.fixture
def router_with_openrouter(tmp_config):
    """Router with OpenRouter configured."""
    config = {
        "providers": [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "provider_type": "openrouter",
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": "sk-or-v1-test123",
                "models": [
                    {"id": "openai/gpt-4o", "name": "GPT-4o"},
                    {"id": "anthropic/claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
                ],
                "capabilities": {"thinking": True, "vision": True, "file_upload": False},
                "auto_discover": True,
            }
        ],
        "active_model": "openai/gpt-4o",
        "active_provider": "openrouter",
    }
    with open(tmp_config, "w") as f:
        json.dump(config, f)
    return ModelRouter(tmp_config)


@pytest.fixture
def router_with_multi_provider(tmp_config):
    """Router with multiple providers."""
    config = {
        "providers": [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "provider_type": "openrouter",
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": "sk-or-v1-test",
                "models": [{"id": "openai/gpt-4o", "name": "GPT-4o"}],
                "capabilities": {"thinking": True, "vision": True, "file_upload": False},
            },
            {
                "id": "kimi",
                "name": "Kimi (Moonshot)",
                "provider_type": "openai_compatible",
                "api_base": "https://api.moonshot.cn/v1",
                "api_key": "sk-kimi-test",
                "models": ["kimi-k3", "kimi-k2-0711-128k"],
                "capabilities": {"thinking": True, "vision": True, "file_upload": True},
            },
        ],
        "active_model": "openai/gpt-4o",
        "active_provider": "openrouter",
    }
    with open(tmp_config, "w") as f:
        json.dump(config, f)
    return ModelRouter(tmp_config)


# ===== Legacy Config Migration =====

class TestLegacyConfigMigration:
    """Test migration from old single-provider format to new multi-provider format."""

    def test_migrates_kimi_config(self, tmp_config):
        """Old Kimi config migrates to a kimi provider."""
        old_config = {
            "api_base": "https://api.moonshot.cn/v1",
            "api_key": "sk-test-key-12345",
            "model": "kimi-k3"
        }
        with open(tmp_config, "w") as f:
            json.dump(old_config, f)

        router = ModelRouter(tmp_config)
        assert len(router.config.providers) == 1
        p = router.config.providers[0]
        assert p.id == "kimi"
        assert p.provider_type == "openai_compatible"
        assert p.api_key == "sk-test-key-12345"
        assert p.capabilities["file_upload"] is True
        assert router.config.active_model == "kimi-k3"

    def test_migrates_openai_config(self, tmp_config):
        """Old OpenAI config migrates correctly."""
        old_config = {
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-openai-xyz",
            "model": "gpt-4o"
        }
        with open(tmp_config, "w") as f:
            json.dump(old_config, f)

        router = ModelRouter(tmp_config)
        p = router.config.providers[0]
        assert p.id == "openai"
        assert p.capabilities["vision"] is True
        assert p.capabilities["file_upload"] is False
        assert router.config.active_model == "gpt-4o"

    def test_migrates_deepseek_config(self, tmp_config):
        """Old DeepSeek config migrates correctly."""
        old_config = {
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "sk-ds-abc",
            "model": "deepseek-chat"
        }
        with open(tmp_config, "w") as f:
            json.dump(old_config, f)

        router = ModelRouter(tmp_config)
        p = router.config.providers[0]
        assert p.id == "deepseek"
        assert p.capabilities["thinking"] is True
        assert p.capabilities["vision"] is False

    def test_migrates_unknown_host(self, tmp_config):
        """Unknown host migrates as custom provider."""
        old_config = {
            "api_base": "https://my-proxy.example.com/v1",
            "api_key": "sk-custom",
            "model": "some-model"
        }
        with open(tmp_config, "w") as f:
            json.dump(old_config, f)

        router = ModelRouter(tmp_config)
        p = router.config.providers[0]
        assert p.id == "custom"
        assert p.api_base == "https://my-proxy.example.com/v1"

    def test_migration_persists_to_disk(self, tmp_config):
        """Migration saves new format to disk."""
        old_config = {"api_base": "https://api.moonshot.cn/v1", "api_key": "sk-x", "model": "kimi-k3"}
        with open(tmp_config, "w") as f:
            json.dump(old_config, f)

        ModelRouter(tmp_config)

        # Re-read from disk
        with open(tmp_config) as f:
            saved = json.load(f)
        assert "providers" in saved
        assert "api_base" not in saved  # Old keys gone


# ===== Model Routing =====

class TestModelRouting:
    """Test that models route to the correct provider."""

    def test_routes_to_openrouter_by_slash_format(self, router_with_openrouter):
        """Models with / in ID route to OpenRouter."""
        p = router_with_openrouter.get_provider_for_model("openai/gpt-4o")
        assert p.id == "openrouter"

    def test_routes_unknown_slash_model_to_openrouter(self, router_with_openrouter):
        """Unknown model with / format still routes to OpenRouter."""
        p = router_with_openrouter.get_provider_for_model("meta-llama/llama-3.3-70b-instruct")
        assert p.id == "openrouter"

    def test_routes_kimi_model_to_kimi(self, router_with_multi_provider):
        """Kimi models route to direct Kimi provider."""
        p = router_with_multi_provider.get_provider_for_model("kimi-k3")
        assert p.id == "kimi"

    def test_routes_slash_model_to_openrouter(self, router_with_multi_provider):
        """Slash models route to OpenRouter even with multi-provider."""
        p = router_with_multi_provider.get_provider_for_model("deepseek/deepseek-chat")
        assert p.id == "openrouter"

    def test_empty_model_uses_active(self, router_with_multi_provider):
        """Empty model ID falls back to active provider."""
        p = router_with_multi_provider.get_provider_for_model("")
        assert p is not None  # Returns active provider

    def test_fallback_to_first_with_key(self, tmp_config):
        """Falls back to first provider with key when active has no key."""
        config = {
            "providers": [
                {"id": "empty", "name": "Empty", "provider_type": "openai_compatible",
                 "api_base": "https://api.example.com/v1", "api_key": "", "models": []},
                {"id": "kimi", "name": "Kimi", "provider_type": "openai_compatible",
                 "api_base": "https://api.moonshot.cn/v1", "api_key": "sk-valid",
                 "models": ["kimi-k3"], "capabilities": {}},
            ],
            "active_model": "", "active_provider": "empty",
        }
        with open(tmp_config, "w") as f:
            json.dump(config, f)
        router = ModelRouter(tmp_config)
        p = router._get_active_provider()
        assert p.id == "kimi"


# ===== Available Models =====

class TestAvailableModels:
    """Test model listing for frontend dropdown."""

    def test_returns_all_models_from_configured_providers(self, router_with_multi_provider):
        """All models from all providers with keys are returned."""
        models = router_with_multi_provider.get_available_models()
        model_ids = [m["id"] for m in models]
        assert "openai/gpt-4o" in model_ids
        assert "kimi-k3" in model_ids
        assert "kimi-k2-0711-128k" in model_ids

    def test_models_include_provider_info(self, router_with_multi_provider):
        """Each model includes its provider name."""
        models = router_with_multi_provider.get_available_models()
        kimi_model = next(m for m in models if m["id"] == "kimi-k3")
        assert kimi_model["provider"] == "kimi"
        assert kimi_model["provider_name"] == "Kimi (Moonshot)"

    def test_skips_providers_without_key(self, tmp_config):
        """Providers without API keys are excluded from model list."""
        config = {
            "providers": [
                {"id": "nokey", "name": "No Key", "provider_type": "openai_compatible",
                 "api_base": "https://api.example.com/v1", "api_key": "",
                 "models": ["model-a"], "capabilities": {}},
            ],
            "active_model": "", "active_provider": "nokey",
        }
        with open(tmp_config, "w") as f:
            json.dump(config, f)
        router = ModelRouter(tmp_config)
        assert router.get_available_models() == []


# ===== Request Building =====

class TestBuildRequestParams:
    """Test building API request parameters."""

    def test_builds_correct_url(self, router_with_openrouter):
        url, _, _ = router_with_openrouter.build_request_params("openai/gpt-4o", [])
        assert url == "https://openrouter.ai/api/v1/chat/completions"

    def test_includes_openrouter_headers(self, router_with_openrouter):
        _, headers, _ = router_with_openrouter.build_request_params("openai/gpt-4o", [])
        assert "HTTP-Referer" in headers
        assert "X-OpenRouter-Title" in headers
        assert headers["Authorization"] == "Bearer sk-or-v1-test123"

    def test_no_openrouter_headers_for_direct_provider(self, router_with_multi_provider):
        _, headers, _ = router_with_multi_provider.build_request_params("kimi-k3", [])
        assert "HTTP-Referer" not in headers
        assert headers["Authorization"] == "Bearer sk-kimi-test"

    def test_thinking_enabled(self, router_with_openrouter):
        _, _, payload = router_with_openrouter.build_request_params(
            "openai/gpt-4o", [], thinking=True
        )
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    def test_thinking_disabled(self, router_with_openrouter):
        _, _, payload = router_with_openrouter.build_request_params(
            "openai/gpt-4o", [], thinking=False
        )
        assert payload["thinking"] == {"type": "disabled"}

    def test_thinking_none_omits_param(self, router_with_openrouter):
        _, _, payload = router_with_openrouter.build_request_params(
            "openai/gpt-4o", [], thinking=None
        )
        assert "thinking" not in payload

    def test_raises_on_no_provider(self, tmp_config):
        """Raises ValueError when no provider is configured."""
        config = {"providers": [], "active_model": "", "active_provider": ""}
        with open(tmp_config, "w") as f:
            json.dump(config, f)
        router = ModelRouter(tmp_config)
        with pytest.raises(AIError, match="NO_PROVIDER_AVAILABLE"):
            router.build_request_params("some-model", [])

    def test_payload_has_stream_true(self, router_with_openrouter):
        _, _, payload = router_with_openrouter.build_request_params("openai/gpt-4o", [])
        assert payload["stream"] is True
        assert payload["model"] == "openai/gpt-4o"


# ===== SSRF Validation =====

class TestSSRFValidation:
    """Test API host validation."""

    def test_allows_known_hosts(self):
        config = RouterConfig(providers=[])
        assert validate_api_host("https://api.openai.com/v1", config) is None
        assert validate_api_host("https://openrouter.ai/api/v1", config) is None
        assert validate_api_host("https://api.moonshot.cn/v1", config) is None

    def test_rejects_http(self):
        config = RouterConfig(providers=[])
        error = validate_api_host("http://api.openai.com/v1", config)
        assert error is not None
        assert error["error_code"] == "SSRF_HTTPS_REQUIRED"

    def test_rejects_unknown_host(self):
        config = RouterConfig(providers=[])
        error = validate_api_host("https://evil.example.com/v1", config)
        assert error is not None
        assert error["error_code"] == "SSRF_HOST_BLOCKED"
        assert "evil.example.com" in error["error"]

    def test_allows_configured_provider_host(self):
        """Hosts from configured providers are automatically allowed."""
        provider = ProviderConfig(
            id="custom", name="Custom", provider_type="openai_compatible",
            api_base="https://my-proxy.corp.com/v1", api_key="sk-x", models=[]
        )
        config = RouterConfig(providers=[provider])
        assert validate_api_host("https://my-proxy.corp.com/v1", config) is None


# ===== Provider Management =====

class TestProviderManagement:
    """Test add/remove/update provider operations."""

    def test_add_provider(self, router_with_openrouter):
        new_p = ProviderConfig(
            id="deepseek", name="DeepSeek", provider_type="openai_compatible",
            api_base="https://api.deepseek.com/v1", api_key="sk-ds-test",
            models=["deepseek-chat"]
        )
        router_with_openrouter.add_provider(new_p)
        assert router_with_openrouter.get_provider("deepseek") is not None
        assert len(router_with_openrouter.config.providers) == 2

    def test_remove_provider(self, router_with_multi_provider):
        router_with_multi_provider.remove_provider("kimi")
        assert router_with_multi_provider.get_provider("kimi") is None
        assert len(router_with_multi_provider.config.providers) == 1

    def test_remove_active_provider_switches(self, router_with_multi_provider):
        """Removing active provider switches to the next available one."""
        router_with_multi_provider.remove_provider("openrouter")
        assert router_with_multi_provider.config.active_provider == "kimi"

    def test_update_provider(self, router_with_openrouter):
        result = router_with_openrouter.update_provider("openrouter", {"api_key": "sk-new-key"})
        assert result is True
        p = router_with_openrouter.get_provider("openrouter")
        assert p.api_key == "sk-new-key"

    def test_update_nonexistent_provider(self, router_with_openrouter):
        result = router_with_openrouter.update_provider("nonexistent", {"api_key": "x"})
        assert result is False


# ===== Config API Helper =====

class TestConfigApiHelper:
    """Test the frontend-facing config serialization."""

    def test_masks_api_keys(self, router_with_openrouter):
        result = get_config_for_api(router_with_openrouter)
        key = result["providers"][0]["api_key"]
        assert "****" in key or "*" in key
        assert "test123" not in key  # Original not exposed

    def test_includes_active_model(self, router_with_openrouter):
        result = get_config_for_api(router_with_openrouter)
        assert result["active_model"] == "openai/gpt-4o"
        assert result["active_provider"] == "openrouter"

    def test_includes_models_list(self, router_with_multi_provider):
        result = get_config_for_api(router_with_multi_provider)
        assert len(result["models"]) >= 3  # gpt-4o + kimi-k3 + kimi-k2


# ===== Default Config =====

class TestDefaultConfig:
    """Test behavior when no config file exists."""

    def test_creates_default_with_openrouter(self, tmp_config):
        """When no config exists, creates default with empty OpenRouter."""
        router = ModelRouter(tmp_config)
        assert len(router.config.providers) == 1
        p = router.config.providers[0]
        assert p.id == "openrouter"
        assert p.api_key == ""  # Empty, user needs to configure
        assert p.provider_type == "openrouter"

    def test_default_config_saved_to_disk(self, tmp_config):
        ModelRouter(tmp_config)
        assert os.path.exists(tmp_config)


# ===== SSE Event Formatting =====

class TestSSEEvent:
    """Test SSE event string formatting."""

    def test_formats_content(self):
        result = _sse_event({"content": "hello"})
        assert result == 'data: {"content": "hello"}\n\n'

    def test_formats_chinese(self):
        result = _sse_event({"content": "你好"})
        assert "你好" in result
        assert result.startswith("data: ")
        assert result.endswith("\n\n")

    def test_formats_done(self):
        result = _sse_event({"done": True})
        assert '"done": true' in result


# ===== Active Model =====

class TestActiveModel:
    """Test active model get/set."""

    def test_get_active_model(self, router_with_openrouter):
        assert router_with_openrouter.get_active_model() == "openai/gpt-4o"

    def test_set_active_model_updates_provider(self, router_with_multi_provider):
        router_with_multi_provider.set_active_model("kimi-k3")
        assert router_with_multi_provider.config.active_model == "kimi-k3"
        assert router_with_multi_provider.config.active_provider == "kimi"

    def test_fallback_to_first_model(self, tmp_config):
        """When no active_model set, returns first model of active provider."""
        config = {
            "providers": [
                {"id": "kimi", "name": "Kimi", "provider_type": "openai_compatible",
                 "api_base": "https://api.moonshot.cn/v1", "api_key": "sk-x",
                 "models": ["kimi-k3"], "capabilities": {}},
            ],
            "active_model": "", "active_provider": "kimi",
        }
        with open(tmp_config, "w") as f:
            json.dump(config, f)
        router = ModelRouter(tmp_config)
        assert router.get_active_model() == "kimi-k3"
