"""AI Contact Summary — background generation of one-line contact descriptions.

Uses a thread pool to generate summaries without blocking the main Flask thread.
Summaries are persisted in contacts_cache.json under an `ai_summary` field per contact.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

_summary_cache = {}        # {username: summary_str}
_summary_timestamps = {}   # {username: generated_at_unix}
_cache_lock = threading.Lock()
_file_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai_summary")

CONTACTS_CACHE_FILE = None  # set by init()
_STALE_DAYS = 7


def init(contacts_cache_file: str):
    """Initialize the summary module and load existing summaries from cache."""
    global CONTACTS_CACHE_FILE
    CONTACTS_CACHE_FILE = contacts_cache_file
    _load_from_cache()


def _load_from_cache():
    """Read ai_summary fields from contacts_cache.json into memory."""
    if not CONTACTS_CACHE_FILE or not os.path.exists(CONTACTS_CACHE_FILE):
        return
    try:
        with _file_lock:
            with open(CONTACTS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        contacts = data.get("contacts", [])
        with _cache_lock:
            for c in contacts:
                username = c.get("username", "")
                summary = c.get("ai_summary", "")
                ts = c.get("ai_summary_ts", 0)
                if username and summary:
                    _summary_cache[username] = summary
                    _summary_timestamps[username] = ts
    except Exception:
        pass


def get_summary(username: str) -> str:
    """Return the cached summary string, or empty string if not available."""
    with _cache_lock:
        return _summary_cache.get(username, "")


def get_all_summaries() -> dict:
    """Return {username: summary} dict for all cached summaries."""
    with _cache_lock:
        return dict(_summary_cache)


def schedule_summary(username: str, display_name: str, api_url: str, headers: dict, model_id: str):
    """Schedule background summary generation if the cached summary is stale or missing."""
    with _cache_lock:
        ts = _summary_timestamps.get(username, 0)
        age_days = (time.time() - ts) / 86400
        if ts > 0 and age_days < _STALE_DAYS:
            return  # still fresh

    _executor.submit(_generate_summary, username, display_name, api_url, headers, model_id)


def _generate_summary(username: str, display_name: str, api_url: str, headers: dict, model_id: str):
    """Worker: call LLM to generate a one-line summary, persist to cache."""
    try:
        # Import lazily to avoid circular import
        import app as wechat_app
        messages = wechat_app.get_messages(username, limit=50, skip_images=True)
        if not messages:
            return

        # Build compact conversation text
        lines = []
        for msg in messages[-30:]:  # use last 30 messages for summary
            sender = "我" if msg["is_self"] else (msg.get("sender_name") or display_name)
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                lines.append(f"{sender}: {content[:80]}")

        if not lines:
            return

        conversation_text = "\n".join(lines)
        prompt = (
            f"以下是与「{display_name}」的微信聊天记录片段：\n\n"
            f"{conversation_text}\n\n"
            "请用一句话（20字以内）概括这段聊天关系的特征或主要话题，不要提名字。"
            "只输出这一句话，不要有任何其他内容。"
        )

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 60,
        }

        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return

        summary = choices[0].get("message", {}).get("content", "").strip()
        if not summary:
            return

        # Update in-memory cache
        with _cache_lock:
            _summary_cache[username] = summary
            _summary_timestamps[username] = time.time()

        # Persist to contacts_cache.json
        _persist_summary(username, summary)

    except Exception as e:
        print(f"[AI Summary] Error generating summary for {username}: {e}")


def _persist_summary(username: str, summary: str):
    """Thread-safe write of ai_summary field to contacts_cache.json."""
    if not CONTACTS_CACHE_FILE or not os.path.exists(CONTACTS_CACHE_FILE):
        return
    try:
        with _file_lock:
            with open(CONTACTS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            contacts = data.get("contacts", [])
            for c in contacts:
                if c.get("username") == username:
                    c["ai_summary"] = summary
                    c["ai_summary_ts"] = time.time()
                    break
            data["contacts"] = contacts
            with open(CONTACTS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AI Summary] Error persisting summary for {username}: {e}")
