/**
 * AI Settings - Global shared component
 * Include this script on any page to get the AI settings button + modal.
 * Call `openAISettings()` to show the modal.
 */
(function() {
    'use strict';

    // Inject CSS
    const style = document.createElement('style');
    style.textContent = `
        .ai-settings-overlay {
            display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5); z-index: 9999; align-items: center; justify-content: center;
            pointer-events: none;
        }
        .ai-settings-overlay.show { display: flex; pointer-events: auto; }
        .ai-settings-modal {
            background: var(--surface, #252535); border-radius: 12px; padding: 24px;
            width: 420px; max-width: 90vw; max-height: 80vh; overflow-y: auto;
            border: 1px solid var(--border, #313244); color: var(--text, #cdd6f4);
        }
        .ai-settings-modal h2 {
            font-size: 16px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;
        }
        .ai-settings-modal .close-btn { cursor: pointer; font-size: 18px; color: var(--text-dim, #a6adc8); }
        .ai-settings-modal .close-btn:hover { color: var(--text, #cdd6f4); }
        .ai-settings-tabs {
            display: flex; gap: 0; margin-bottom: 16px; border-bottom: 1px solid var(--border, #313244);
        }
        .ai-settings-tab {
            padding: 8px 16px; font-size: 13px; cursor: pointer; border: none;
            background: none; color: var(--text-dim, #a6adc8); border-bottom: 2px solid transparent;
        }
        .ai-settings-tab.active { color: var(--accent, #5b6abf); border-bottom-color: var(--accent, #5b6abf); }
        .ai-settings-panel { display: none; }
        .ai-settings-panel.active { display: block; }
        .ai-settings-modal .form-group { margin-bottom: 14px; }
        .ai-settings-modal .form-group label { display: block; font-size: 12px; color: var(--text-dim, #a6adc8); margin-bottom: 4px; }
        .ai-settings-modal .form-group input {
            width: 100%; padding: 8px 12px; border-radius: 6px;
            border: 1px solid var(--border, #313244); background: var(--bg, #1e1e2e);
            color: var(--text, #cdd6f4); font-size: 13px;
        }
        .ai-settings-modal .form-group input:focus { border-color: var(--accent, #5b6abf); outline: none; }
        .ai-settings-modal .hint { font-size: 11px; color: var(--text-dim, #a6adc8); margin-top: 4px; }
        .ai-settings-modal .hint a { color: var(--accent, #5b6abf); }
        .ai-settings-modal .btn-row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
        .ai-settings-modal .btn {
            padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none;
        }
        .ai-settings-modal .btn-primary { background: var(--accent, #5b6abf); color: #fff; }
        .ai-settings-modal .btn-primary:hover { opacity: 0.9; }
        .ai-settings-modal .btn-ghost { background: none; border: 1px solid var(--border, #313244); color: var(--text-dim, #a6adc8); }
        .ai-settings-modal .btn-discover {
            padding: 8px 14px; background: var(--bg, #1e1e2e); border: 1px solid var(--border, #313244);
            border-radius: 8px; color: var(--text-dim, #a6adc8); cursor: pointer; font-size: 12px;
        }
        .ai-settings-modal .btn-discover:hover { border-color: var(--accent, #5b6abf); color: var(--accent, #5b6abf); }
        .ai-settings-modal .btn-discover:disabled { opacity: 0.5; cursor: not-allowed; }
        .ai-settings-toast {
            position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
            background: var(--surface, #252535); color: var(--text, #cdd6f4);
            padding: 10px 20px; border-radius: 8px; border: 1px solid var(--border, #313244);
            font-size: 13px; z-index: 10000; opacity: 0; transition: opacity 0.3s;
            pointer-events: none;
        }
        .ai-settings-toast.show { opacity: 1; }
    `;
    document.head.appendChild(style);

    // Inject Modal HTML
    const modalHTML = `
    <div class="ai-settings-overlay" id="aiSettingsModal">
        <div class="ai-settings-modal">
            <h2>AI 设置 <span class="close-btn" onclick="closeAISettings()">✕</span></h2>
            <div class="ai-settings-tabs">
                <button class="ai-settings-tab active" onclick="switchAISettingsTab('openrouter')">OpenRouter (推荐)</button>
                <button class="ai-settings-tab" onclick="switchAISettingsTab('custom')">自定义接口</button>
            </div>
            <div class="ai-settings-panel active" id="aiPanelOpenrouter">
                <div class="form-group">
                    <label>OpenRouter API Key</label>
                    <input type="password" id="cfgOpenrouterKey" placeholder="sk-or-v1-...">
                    <div class="hint">一个 Key 访问 400+ 模型 — 注册 <a href="https://openrouter.ai" target="_blank">openrouter.ai</a> 获取</div>
                </div>
                <div class="btn-row" style="justify-content:space-between">
                    <button class="btn-discover" id="btnDiscover" onclick="discoverModels()">发现可用模型</button>
                    <button class="btn btn-primary" onclick="saveOpenrouterConfig()">保存</button>
                </div>
                <div id="aiDiscoverStatus" style="font-size:12px;color:var(--text-dim);margin-top:10px;display:none;"></div>
            </div>
            <div class="ai-settings-panel" id="aiPanelCustom">
                <div class="form-group">
                    <label>API Base URL</label>
                    <input type="text" id="cfgAiSetApiBase" placeholder="https://api.moonshot.cn/v1">
                    <div class="hint">支持 Kimi、OpenAI、DeepSeek 等 OpenAI 兼容接口</div>
                </div>
                <div class="form-group">
                    <label>API Key</label>
                    <input type="password" id="cfgAiSetApiKey" placeholder="sk-...">
                </div>
                <div class="form-group">
                    <label>Model</label>
                    <input type="text" id="cfgAiSetModel" placeholder="kimi-k3">
                    <div class="hint">Kimi: kimi-k3 | GPT-4o: gpt-4o | DeepSeek: deepseek-chat</div>
                </div>
                <div class="btn-row">
                    <button class="btn btn-ghost" onclick="closeAISettings()">取消</button>
                    <button class="btn btn-primary" onclick="saveCustomConfig()">保存</button>
                </div>
            </div>
        </div>
    </div>
    <div class="ai-settings-toast" id="aiSettingsToast"></div>
    `;

    const container = document.createElement('div');
    container.innerHTML = modalHTML;
    // Use position:fixed elements — append directly without a wrapper that could
    // break flex layouts. Extract children and append them individually.
    while (container.firstChild) {
        document.body.appendChild(container.firstChild);
    }

    // === Toast ===
    function showAISettingsToast(msg) {
        const t = document.getElementById('aiSettingsToast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 3000);
    }

    // === Tab switching ===
    window.switchAISettingsTab = (tab) => {
        document.querySelectorAll('.ai-settings-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.ai-settings-panel').forEach(p => p.classList.remove('active'));
        if (tab === 'openrouter') {
            document.querySelectorAll('.ai-settings-tab')[0].classList.add('active');
            document.getElementById('aiPanelOpenrouter').classList.add('active');
        } else {
            document.querySelectorAll('.ai-settings-tab')[1].classList.add('active');
            document.getElementById('aiPanelCustom').classList.add('active');
        }
    };

    // === Open / Close ===
    window.openAISettings = () => {
        loadAIConfig();
        document.getElementById('aiSettingsModal').classList.add('show');
    };
    window.closeAISettings = () => {
        document.getElementById('aiSettingsModal').classList.remove('show');
    };

    // === Load current config ===
    function loadAIConfig() {
        fetch('/api/ai/config').then(r => r.json()).then(cfg => {
            if (cfg.providers) {
                const orProvider = cfg.providers.find(p => p.provider_type === 'openrouter');
                if (orProvider) {
                    document.getElementById('cfgOpenrouterKey').value = '';
                    document.getElementById('cfgOpenrouterKey').placeholder = orProvider.api_key || 'sk-or-v1-...';
                }
                const customProvider = cfg.providers.find(p => p.provider_type !== 'openrouter');
                if (customProvider) {
                    document.getElementById('cfgAiSetApiBase').value = customProvider.api_base || '';
                    document.getElementById('cfgAiSetApiKey').value = '';
                    document.getElementById('cfgAiSetApiKey').placeholder = customProvider.api_key || 'sk-...';
                    document.getElementById('cfgAiSetModel').value = '';
                }
            } else {
                // Legacy format
                document.getElementById('cfgAiSetApiBase').value = cfg.api_base || '';
                document.getElementById('cfgAiSetApiKey').placeholder = cfg.api_key || 'sk-...';
                document.getElementById('cfgAiSetModel').value = cfg.model || '';
            }
        }).catch(() => {});
    }

    // === Save OpenRouter ===
    window.saveOpenrouterConfig = async () => {
        const key = document.getElementById('cfgOpenrouterKey').value.trim();
        if (!key) { showAISettingsToast('请输入 OpenRouter API Key'); return; }

        // Always use add_provider (it replaces existing provider with same id)
        const resp = await fetch('/api/ai/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                action: 'add_provider',
                provider: {
                    id: 'openrouter',
                    name: 'OpenRouter',
                    provider_type: 'openrouter',
                    api_base: 'https://openrouter.ai/api/v1',
                    api_key: key,
                    models: [],
                    capabilities: {thinking: true, vision: true, file_upload: false},
                    auto_discover: true
                }
            })
        });

        if (resp.ok) {
            showAISettingsToast('OpenRouter Key 已保存');
            closeAISettings();
            discoverModels();
            document.dispatchEvent(new CustomEvent('ai-config-changed'));
        } else {
            const err = await resp.json();
            showAISettingsToast('保存失败: ' + (err.error || '未知错误'));
        }
    };

    // === Save Custom ===
    window.saveCustomConfig = async () => {
        const data = {
            api_base: document.getElementById('cfgAiSetApiBase').value.trim(),
            model: document.getElementById('cfgAiSetModel').value.trim(),
        };
        const key = document.getElementById('cfgAiSetApiKey').value.trim();
        if (key) data.api_key = key;

        if (!data.api_base) { showAISettingsToast('请填写 API Base URL'); return; }

        const resp = await fetch('/api/ai/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        if (resp.ok) {
            closeAISettings();
            showAISettingsToast('设置已保存');
            document.dispatchEvent(new CustomEvent('ai-config-changed'));
        } else {
            const err = await resp.json();
            showAISettingsToast('保存失败: ' + (err.error || '未知错误'));
        }
    };

    // === Discover Models ===
    window.discoverModels = async () => {
        const btn = document.getElementById('btnDiscover');
        const status = document.getElementById('aiDiscoverStatus');
        if (btn) btn.disabled = true;
        if (btn) btn.textContent = '⏳ 发现中...';
        if (status) {
            status.style.display = 'block';
            status.textContent = '正在查询可用模型...';
        }

        try {
            const resp = await fetch('/api/ai/models/discover', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider_id: 'openrouter'})
            });
            const data = await resp.json();
            if (data.error) {
                if (status) status.textContent = '❌ ' + data.error;
                showAISettingsToast(data.error);
            } else {
                const count = data.models ? data.models.length : 0;
                if (status) status.textContent = `✅ 发现 ${count} 个可用模型`;
                showAISettingsToast(`已发现 ${count} 个可用模型`);
                document.dispatchEvent(new CustomEvent('ai-models-discovered', {detail: data.models}));
            }
        } catch(e) {
            if (status) status.textContent = '❌ 网络错误: ' + e.message;
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '发现可用模型';
            }
        }
    };

    // Click outside to close
    document.getElementById('aiSettingsModal').addEventListener('click', function(e) {
        if (e.target === this) closeAISettings();
    });

})();
