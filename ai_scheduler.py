"""AI Scheduler — background scheduled agent tasks.

Users can create recurring tasks (daily/weekly/on_sync/interval) that run
the agent loop automatically and push results as SSE events.

Schedule formats:
  "daily@HH:MM"           — every day at that local time
  "weekly@DOW@HH:MM"      — weekly (DOW: mon/tue/wed/thu/fri/sat/sun)
  "on_sync"               — after every successful sync
  "interval@Nh"           — every N hours
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional


TASK_FILE = None      # set by init()
_engine: Optional["SchedulerEngine"] = None


def init(config_dir: str, model_router, push_event_fn):
    global TASK_FILE, _engine
    TASK_FILE = os.path.join(config_dir, "scheduled_tasks.json")
    _engine = SchedulerEngine(config_dir, model_router, push_event_fn)
    _engine.start()
    return _engine


def get_engine() -> Optional["SchedulerEngine"]:
    return _engine


# ───────────────────────── ScheduledTask ─────────────────────────────


class ScheduledTask:
    def __init__(self, id=None, name="", prompt="", schedule="daily@08:00",
                 contacts=None, enabled=True, last_run=0.0, next_run=0.0, history=None):
        self.id = id or uuid.uuid4().hex[:8]
        self.name = name
        self.prompt = prompt
        self.schedule = schedule
        self.contacts = contacts or []
        self.enabled = enabled
        self.last_run = last_run
        self.next_run = next_run or _compute_next_run(schedule, time.time())
        self.history = history or []  # [{timestamp, result_summary, success}] last 10

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": self.schedule,
            "contacts": self.contacts,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        return cls(**{k: d[k] for k in d if k in cls.__init__.__code__.co_varnames})


# ───────────────────────── Schedule Math ─────────────────────────────


def _compute_next_run(schedule: str, after: float) -> float:
    """Compute the next fire timestamp for a schedule string."""
    now = datetime.fromtimestamp(after)
    parts = schedule.split("@")

    if parts[0] == "daily" and len(parts) == 2:
        h, m = map(int, parts[1].split(":"))
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate.timestamp() <= after:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    if parts[0] == "weekly" and len(parts) == 3:
        dow_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        target_dow = dow_map.get(parts[1].lower(), 0)
        h, m = map(int, parts[2].split(":"))
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        days_ahead = (target_dow - candidate.weekday()) % 7
        if days_ahead == 0 and candidate.timestamp() <= after:
            days_ahead = 7
        candidate += timedelta(days=days_ahead)
        return candidate.timestamp()

    if parts[0] == "interval" and len(parts) == 2:
        hours = float(parts[1].rstrip("h"))
        return after + hours * 3600

    if parts[0] == "on_sync":
        return float("inf")  # triggered externally, not by time

    # Default: 24h from now
    return after + 86400


# ───────────────────────── SchedulerEngine ───────────────────────────


class SchedulerEngine:
    def __init__(self, config_dir: str, model_router, push_event_fn):
        self._config_dir = config_dir
        self._model_router = model_router
        self._push_event = push_event_fn
        self._tasks: dict[str, ScheduledTask] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._load()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ai_scheduler")
        self._thread.start()

    def _run_loop(self):
        while self._running:
            now = time.time()
            with self._lock:
                due = [t for t in self._tasks.values()
                       if t.enabled and t.schedule != "on_sync" and t.next_run <= now]
            for task in due:
                threading.Thread(target=self._fire_task, args=(task,), daemon=True).start()
            time.sleep(60)

    def trigger_on_sync(self):
        """Trigger all on_sync tasks (called after a successful sync)."""
        with self._lock:
            on_sync_tasks = [t for t in self._tasks.values()
                             if t.enabled and t.schedule == "on_sync"]
        for task in on_sync_tasks:
            threading.Thread(target=self._fire_task, args=(task,), daemon=True).start()

    def _fire_task(self, task: ScheduledTask):
        """Execute a scheduled task and push SSE result."""
        start_time = time.time()
        try:
            provider = self._model_router._get_active_provider()
            if not provider or not provider.api_key:
                return

            from ai_router import AIError
            api_url, headers, payload = self._model_router.build_request_params(
                self._model_router.get_active_model(), [], thinking=None
            )

            # Build messages with optional contact context
            import app as wechat_app
            from ai_agent import make_default_registry, run_agent_loop

            api_messages = [
                {"role": "system", "content": "请始终使用中文回答。"},
            ]

            if task.contacts:
                context_lines = []
                for uname in task.contacts:
                    msgs = wechat_app.get_messages(uname, limit=100, skip_images=True)
                    if msgs:
                        all_contacts = wechat_app.get_all_contacts()
                        contact_map = {c["username"]: c for c in all_contacts}
                        info = contact_map.get(uname, {})
                        name = info.get("remark") or info.get("nick_name") or uname
                        context_lines.append(f"\n--- 与「{name}」的聊天记录 ---")
                        for m in msgs:
                            sender = "我" if m["is_self"] else (m.get("sender_name") or name)
                            context_lines.append(f"[{m['time_str']}] {sender}: {m['content']}")
                if context_lines:
                    api_messages.append({"role": "system", "content": "\n".join(context_lines)})

            api_messages.append({"role": "user", "content": task.prompt})
            payload["messages"] = api_messages

            # Collect full agent output (don't stream)
            registry = make_default_registry(task.contacts) if task.contacts else None
            result_parts = []

            if registry:
                for event_str in run_agent_loop(api_url, headers, payload, api_messages, registry):
                    import re as _re
                    m = _re.match(r"data: (.+)", event_str)
                    if m:
                        try:
                            d = json.loads(m.group(1))
                            if d.get("content"):
                                result_parts.append(d["content"])
                        except Exception:
                            pass
            else:
                # Direct LLM call
                from ai_agent import stream_and_collect_tool_calls
                content, _, _, _ = stream_and_collect_tool_calls(api_url, headers, payload)
                result_parts.append(content)

            result_text = "".join(result_parts)
            summary = result_text[:200] + ("..." if len(result_text) > 200 else "")
            success = bool(result_text.strip())

        except Exception as e:
            summary = f"执行失败: {e}"
            success = False

        # Update task state
        with self._lock:
            t = self._tasks.get(task.id)
            if t:
                t.last_run = start_time
                t.next_run = _compute_next_run(t.schedule, time.time())
                entry = {"timestamp": start_time, "result_summary": summary, "success": success}
                t.history.insert(0, entry)
                t.history = t.history[:10]
        self._save()

        # Push SSE notification
        self._push_event({
            "type": "agent_task_result",
            "task_id": task.id,
            "task_name": task.name,
            "success": success,
            "summary": summary,
        })

    # ── CRUD ──

    def list_tasks(self) -> list:
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(task_id)
            return t.to_dict() if t else None

    def create_task(self, name: str, prompt: str, schedule: str,
                    contacts: list = None, enabled: bool = True) -> dict:
        task = ScheduledTask(name=name, prompt=prompt, schedule=schedule,
                             contacts=contacts or [], enabled=enabled)
        with self._lock:
            self._tasks[task.id] = task
        self._save()
        return task.to_dict()

    def update_task(self, task_id: str, updates: dict) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return False
            for k, v in updates.items():
                if hasattr(t, k) and k not in ("id", "history"):
                    setattr(t, k, v)
            if "schedule" in updates:
                t.next_run = _compute_next_run(t.schedule, time.time())
        self._save()
        return True

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
        self._save()
        return True

    def run_now(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
        if not t:
            return False
        threading.Thread(target=self._fire_task, args=(t,), daemon=True).start()
        return True

    def _load(self):
        if not TASK_FILE or not os.path.exists(TASK_FILE):
            return
        try:
            with open(TASK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("tasks", []):
                t = ScheduledTask.from_dict(d)
                self._tasks[t.id] = t
        except Exception as e:
            print(f"[Scheduler] Load error: {e}")

    def _save(self):
        if not TASK_FILE:
            return
        try:
            os.makedirs(os.path.dirname(TASK_FILE), exist_ok=True)
            with self._lock:
                data = {"tasks": [t.to_dict() for t in self._tasks.values()]}
            with open(TASK_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Scheduler] Save error: {e}")
