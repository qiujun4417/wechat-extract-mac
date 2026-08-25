"""AI Cross-Session Memory — extract and persist insights from conversations.

After each session with 4+ messages, a background thread extracts key insights
and stores them per contact in ai_memory.json. Future sessions inject the
relevant memories into the system prompt automatically.
"""

import json
import os
import threading
import time

import requests

_memory = {}       # {username: [{summary, timestamp, session_id}]}
_lock = threading.Lock()
_MEMORY_FILE = None
_MAX_ENTRIES_PER_CONTACT = 10
_EXPIRE_DAYS = 30


def init(config_dir: str):
    """Initialize and load memory from disk."""
    global _MEMORY_FILE
    _MEMORY_FILE = os.path.join(config_dir, "ai_memory.json")
    _load()


def _load():
    if not _MEMORY_FILE or not os.path.exists(_MEMORY_FILE):
        return
    try:
        with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _memory.update(data)
    except Exception:
        pass


def _save():
    if not _MEMORY_FILE:
        return
    try:
        os.makedirs(os.path.dirname(_MEMORY_FILE), exist_ok=True)
        with _lock:
            snapshot = {k: list(v) for k, v in _memory.items()}
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AI Memory] Error saving: {e}")


def get_relevant_memory(contact_usernames: list) -> str:
    """Return formatted memory string for injection into system prompt.

    Returns up to 3 recent entries per contact, ignoring entries older than
    EXPIRE_DAYS. Returns empty string if nothing relevant.
    """
    if not contact_usernames:
        return ""

    now = time.time()
    expire_cutoff = now - _EXPIRE_DAYS * 86400
    parts = []

    with _lock:
        for username in contact_usernames:
            entries = _memory.get(username, [])
            recent = [
                e for e in entries
                if e.get("timestamp", 0) > expire_cutoff
            ]
            recent.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
            for entry in recent[:3]:
                parts.append(f"- {entry['summary']}")

    if not parts:
        return ""

    return (
        "【历史对话记忆】以下是你与用户在此联系人相关的历史对话中提炼的关键信息，"
        "请在本次对话中作为背景知识参考：\n" + "\n".join(parts)
    )


def get_all_memories() -> dict:
    """Return all memories as {username: [entries]} dict."""
    with _lock:
        return {k: list(v) for k, v in _memory.items()}


def delete_memory(username: str = None):
    """Delete memory for a specific contact, or clear all memories if username is None."""
    with _lock:
        if username:
            _memory.pop(username, None)
        else:
            _memory.clear()
    _save()


def extract_memory_async(
    messages: list,
    contact_usernames: list,
    session_id: str,
    api_url: str,
    headers: dict,
    model_id: str,
):
    """Spawn a daemon thread to extract insights from the conversation."""
    if len(messages) < 4:
        return
    if not contact_usernames:
        return

    t = threading.Thread(
        target=_extract_and_store,
        args=(messages, contact_usernames, session_id, api_url, headers, model_id),
        daemon=True,
    )
    t.start()


def _extract_and_store(
    messages: list,
    contact_usernames: list,
    session_id: str,
    api_url: str,
    headers: dict,
    model_id: str,
):
    """Worker: call LLM to summarize session insights, store per contact."""
    try:
        # Build compact conversation text (last 20 messages)
        lines = []
        for msg in messages[-20:]:
            role = "用户" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                lines.append(f"{role}: {content[:200]}")

        if not lines:
            return

        conversation_text = "\n".join(lines)
        prompt = (
            "以下是一段关于微信聊天分析的对话记录：\n\n"
            f"{conversation_text}\n\n"
            "请提炼出这段对话中的关键信息点（如用户关注的话题、分析结论、偏好等），"
            "用一句话概括（30字以内）。只输出这一句话，不要有任何前缀或解释。"
        )

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 80,
        }

        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return

        summary = choices[0].get("message", {}).get("content", "").strip()
        if not summary or len(summary) > 100:
            return

        entry = {
            "summary": summary,
            "timestamp": time.time(),
            "session_id": session_id,
        }

        with _lock:
            for username in contact_usernames:
                entries = _memory.setdefault(username, [])
                # Avoid duplicate summaries from the same session
                if any(e.get("session_id") == session_id for e in entries):
                    continue
                entries.append(entry)
                # Keep only the most recent MAX_ENTRIES_PER_CONTACT
                entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
                _memory[username] = entries[:_MAX_ENTRIES_PER_CONTACT]

        _save()
        print(f"[AI Memory] Saved insight for {contact_usernames}: {summary[:60]}")

    except Exception as e:
        print(f"[AI Memory] Error extracting memory: {e}")
