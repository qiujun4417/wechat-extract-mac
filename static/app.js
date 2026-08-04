/**
 * WeChat Export - Frontend Application
 */

(function() {
    'use strict';

    // State
    let contacts = [];
    let selectedUsernames = new Set();
    let currentFilter = 'all';
    let searchQuery = '';
    let activeContact = null;

    // DOM Elements
    const contactsList = document.getElementById('contactsList');
    const searchInput = document.getElementById('searchInput');
    const selectAllBtn = document.getElementById('selectAllBtn');
    const deselectAllBtn = document.getElementById('deselectAllBtn');
    const exportBtn = document.getElementById('exportBtn');
    const exportFormat = document.getElementById('exportFormat');
    const selectedCount = document.getElementById('selectedCount');
    const chatPanel = document.getElementById('chatPanel');
    const toast = document.getElementById('toast');

    // Initialize
    init();

    function init() {
        // Check if we need to force refresh (after sync)
        const urlParams = new URLSearchParams(window.location.search);
        const needRefresh = urlParams.has('refresh');
        loadContacts(needRefresh);
        bindEvents();
        // Clean up URL
        if (needRefresh) {
            window.history.replaceState({}, '', '/');
        }
    }

    function bindEvents() {
        searchInput.addEventListener('input', debounce(handleSearch, 200));
        selectAllBtn.addEventListener('click', selectAllVisible);
        deselectAllBtn.addEventListener('click', deselectAll);
        exportBtn.addEventListener('click', handleExport);

        // Filter tabs
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentFilter = tab.dataset.filter;
                renderContacts();
            });
        });
    }

    // ===== Data Loading =====

    async function loadContacts(refresh) {
        try {
            const url = refresh ? '/api/contacts?refresh=1' : '/api/contacts';
            const response = await fetch(url);
            contacts = await response.json();
            renderContacts();
        } catch (err) {
            contactsList.innerHTML = '<div class="loading" style="color:#f38ba8">Failed to load contacts</div>';
            console.error('Failed to load contacts:', err);
        }
    }

    // ===== Rendering =====

    function getVisibleContacts() {
        let filtered = contacts;

        // Apply type filter
        if (currentFilter === 'contacts') {
            filtered = filtered.filter(c => !c.is_group);
        } else if (currentFilter === 'groups') {
            filtered = filtered.filter(c => c.is_group);
        }

        // Apply search
        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            filtered = filtered.filter(c =>
                (c.display_name && c.display_name.toLowerCase().includes(q)) ||
                (c.nick_name && c.nick_name.toLowerCase().includes(q)) ||
                (c.remark && c.remark.toLowerCase().includes(q)) ||
                (c.username && c.username.toLowerCase().includes(q))
            );
        }

        return filtered;
    }

    function renderContacts() {
        const visible = getVisibleContacts();

        if (visible.length === 0) {
            contactsList.innerHTML = '<div class="loading" style="animation:none">No contacts found</div>';
            return;
        }

        const html = visible.map(contact => {
            const isSelected = selectedUsernames.has(contact.username);
            const isActive = activeContact === contact.username;
            const initial = (contact.display_name || '?')[0].toUpperCase();

            return `
                <div class="contact-item ${isActive ? 'active' : ''}"
                     data-username="${escapeAttr(contact.username)}">
                    <div class="checkbox ${isSelected ? 'checked' : ''}"
                         data-action="toggle"></div>
                    <div class="contact-avatar">
                        ${contact.avatar
                            ? `<img src="${escapeAttr(contact.avatar)}" alt="" onerror="this.parentElement.innerHTML='<span class=initial>${initial}</span>'">`
                            : `<span class="initial">${initial}</span>`
                        }
                    </div>
                    <div class="contact-details">
                        <div class="contact-name">${escapeHtml(contact.display_name)}</div>
                        <div class="contact-meta">
                            ${contact.is_group ? '<span class="contact-badge group">Group</span>' : ''}
                            ${contact.is_official ? '<span class="contact-badge official">公众号</span>' : ''}
                            <span>${contact.message_count} msgs</span>
                            <span>${contact.last_active}</span>
                        </div>
                    </div>
                    <div class="contact-count">${formatCount(contact.message_count)}</div>
                </div>
            `;
        }).join('');

        contactsList.innerHTML = html;

        // Bind click events
        contactsList.querySelectorAll('.contact-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const username = item.dataset.username;
                if (e.target.closest('[data-action="toggle"]')) {
                    toggleSelection(username);
                } else {
                    previewChat(username);
                }
            });
        });
    }

    // ===== Chat Preview =====
    let currentChatUsername = null;
    let currentChatOffset = 0;
    let isLoadingMore = false;
    let hasMoreMessages = true;
    const MESSAGES_PER_PAGE = 50;

    async function previewChat(username) {
        activeContact = username;
        currentChatUsername = username;
        currentChatOffset = 0;
        hasMoreMessages = true;
        renderContacts();

        const contact = contacts.find(c => c.username === username);
        if (!contact) return;

        chatPanel.innerHTML = '<div class="loading">Loading messages...</div>';

        try {
            const response = await fetch(`/api/messages/${encodeURIComponent(username)}?limit=${MESSAGES_PER_PAGE}&offset=0`);
            const messages = await response.json();
            currentChatOffset = messages.length;
            if (messages.length < MESSAGES_PER_PAGE) hasMoreMessages = false;

            const initial = (contact.display_name || '?')[0].toUpperCase();
            let html = `
                <div class="chat-header">
                    <div class="chat-avatar">
                        ${contact.avatar
                            ? `<img src="${escapeAttr(contact.avatar)}" alt="">`
                            : `<div class="avatar-placeholder">${initial}</div>`
                        }
                    </div>
                    <div class="chat-info">
                        <h2>${escapeHtml(contact.display_name)}</h2>
                        <span class="chat-meta">${contact.message_count} messages &middot; Last active: ${contact.last_active}</span>
                    </div>
                    <a href="/ai?contact=${encodeURIComponent(username)}" class="ai-analyze-btn" title="AI 分析此对话">🤖 AI 分析</a>
                </div>
                <div class="messages-container" id="messagesContainer">
                    ${hasMoreMessages ? '<div class="load-more-trigger" id="loadMoreTrigger">⬆ 上拉加载更多</div>' : ''}
            `;

            if (messages.length === 0) {
                html += '<div class="no-messages">No messages found</div>';
            } else {
                html += renderMessages(messages);
            }

            html += '</div>';
            chatPanel.innerHTML = html;

            // Scroll to bottom
            const container = document.getElementById('messagesContainer');
            if (container) {
                container.scrollTop = container.scrollHeight;
                // Add scroll listener for infinite scroll up
                container.addEventListener('scroll', handleScrollUp);
            }
        } catch (err) {
            chatPanel.innerHTML = '<div class="empty-state"><h2>Failed to load messages</h2></div>';
            console.error('Failed to load messages:', err);
        }
    }

    function renderMessages(messages) {
        let html = '';
        const contact = contacts.find(c => c.username === currentChatUsername);
        const contactAvatar = contact && contact.avatar ? `<img src="${escapeAttr(contact.avatar)}" class="msg-avatar-img">` : '';
        const contactInitial = contact ? (contact.display_name || '?')[0] : '?';

        messages.forEach(msg => {
            const direction = msg.is_self ? 'sent' : 'received';
            let contentHtml = '';
            if (msg.image_url) {
                contentHtml = `<img src="${escapeAttr(msg.image_url)}" class="msg-image" loading="lazy" onclick="window.open(this.src)">`;
            } else if (msg.base_type === 3) {
                contentHtml = `<div class="media-placeholder">🖼️ [Image]</div>`;
            } else if (msg.content && (msg.content.startsWith('<a ') || msg.content.includes('class="msg-link"'))) {
                // Already HTML (links), render as-is
                contentHtml = msg.content;
            } else {
                contentHtml = escapeHtml(msg.content);
            }
            const senderName = msg.sender_name && !msg.is_self ? `<div class="bubble-sender">${escapeHtml(msg.sender_name)}</div>` : '';

            // Avatar for received messages
            let avatarHtml = '';
            if (!msg.is_self) {
                avatarHtml = contactAvatar
                    ? `<div class="msg-avatar">${contactAvatar}</div>`
                    : `<div class="msg-avatar"><span class="msg-avatar-initial">${escapeHtml(msg.sender_name ? msg.sender_name[0] : contactInitial)}</span></div>`;
            }

            html += `
                <div class="message ${direction}">
                    ${avatarHtml}
                    <div class="bubble">
                        ${senderName}
                        <div class="bubble-content">${contentHtml}</div>
                        <div class="bubble-time">${msg.time_str}</div>
                    </div>
                </div>
            `;
        });
        return html;
    }

    async function handleScrollUp() {
        const container = document.getElementById('messagesContainer');
        if (!container || isLoadingMore || !hasMoreMessages) return;

        // Trigger when scrolled near the top (within 100px)
        if (container.scrollTop < 100) {
            isLoadingMore = true;
            const trigger = document.getElementById('loadMoreTrigger');
            if (trigger) trigger.textContent = '加载中...';

            try {
                const response = await fetch(`/api/messages/${encodeURIComponent(currentChatUsername)}?limit=${MESSAGES_PER_PAGE}&offset=${currentChatOffset}`);
                const olderMessages = await response.json();

                if (olderMessages.length === 0) {
                    hasMoreMessages = false;
                    if (trigger) trigger.textContent = '— 没有更多消息了 —';
                } else {
                    currentChatOffset += olderMessages.length;
                    if (olderMessages.length < MESSAGES_PER_PAGE) hasMoreMessages = false;

                    // Remember scroll position
                    const prevScrollHeight = container.scrollHeight;

                    // Insert older messages after the trigger
                    const newHtml = renderMessages(olderMessages);
                    if (trigger) {
                        trigger.insertAdjacentHTML('afterend', newHtml);
                        if (!hasMoreMessages) trigger.textContent = '— 没有更多消息了 —';
                        else trigger.textContent = '⬆ 上拉加载更多';
                    }

                    // Restore scroll position (keep user at same visual position)
                    container.scrollTop = container.scrollHeight - prevScrollHeight;
                }
            } catch (err) {
                console.error('Failed to load more messages:', err);
            }
            isLoadingMore = false;
        }
    }

    // ===== Selection =====

    function toggleSelection(username) {
        if (selectedUsernames.has(username)) {
            selectedUsernames.delete(username);
        } else {
            selectedUsernames.add(username);
        }
        updateSelectionUI();
        renderContacts();
    }

    function selectAllVisible() {
        const visible = getVisibleContacts();
        visible.forEach(c => selectedUsernames.add(c.username));
        updateSelectionUI();
        renderContacts();
        showToast(`Selected ${visible.length} contacts`);
    }

    function deselectAll() {
        selectedUsernames.clear();
        updateSelectionUI();
        renderContacts();
        showToast('Selection cleared');
    }

    function updateSelectionUI() {
        selectedCount.textContent = selectedUsernames.size;
        exportBtn.disabled = selectedUsernames.size === 0;
    }

    // ===== Export =====

    async function handleExport() {
        if (selectedUsernames.size === 0) {
            showToast('No contacts selected');
            return;
        }

        const format = exportFormat.value;
        exportBtn.disabled = true;
        exportBtn.textContent = 'Exporting...';

        try {
            const response = await fetch('/api/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    usernames: Array.from(selectedUsernames),
                    format: format
                })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Export failed');
            }

            // Download the zip file
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `wechat_export.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            showToast(`Exported ${selectedUsernames.size} conversations as ${format.toUpperCase()}`);
        } catch (err) {
            showToast('Export failed: ' + err.message);
            console.error('Export failed:', err);
        } finally {
            exportBtn.disabled = selectedUsernames.size === 0;
            exportBtn.textContent = 'Export';
        }
    }

    // ===== Search =====

    function handleSearch() {
        searchQuery = searchInput.value.trim();
        renderContacts();
    }

    // ===== Utilities =====

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function escapeAttr(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function formatCount(n) {
        if (n >= 10000) return (n / 1000).toFixed(0) + 'k';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
        return n.toString();
    }

    function debounce(fn, delay) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function showToast(message) {
        toast.textContent = message;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 3000);
    }

})();
