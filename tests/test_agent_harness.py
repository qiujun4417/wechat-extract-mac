"""Tests for agent harness modules: ai_agent, ai_memory, ai_scheduler, ai_custom_tools."""

import json
import os
import tempfile
import threading
import time
import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────── ai_agent ────────────────────────────────


from ai_agent import (
    ToolRegistry,
    make_default_registry,
    stream_and_collect_tool_calls,
    run_agent_loop,
    run_plan_and_execute_loop,
    _sse,
    _get_contact_stats,
    _get_contacts_list,
)


class TestToolRegistry:
    def test_register_and_execute(self):
        reg = ToolRegistry()
        reg.register("add", "Add two numbers", {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
        }, lambda a, b: a + b)
        result = json.loads(reg.execute("add", {"a": 3, "b": 4}))
        assert result == 7

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = json.loads(reg.execute("nonexistent", {}))
        assert "error" in result

    def test_execute_wrong_args(self):
        reg = ToolRegistry()
        reg.register("greet", "Greet", {}, lambda name: f"Hello {name}")
        result = json.loads(reg.execute("greet", {}))
        assert "error" in result

    def test_to_openai_tools(self):
        reg = ToolRegistry()
        reg.register("ping", "Ping tool", {"type": "object", "properties": {}}, lambda: "pong")
        tools = reg.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "ping"

    def test_has(self):
        reg = ToolRegistry()
        reg.register("foo", "Foo", {}, lambda: None)
        assert reg.has("foo")
        assert not reg.has("bar")

    def test_mark_requires_approval(self):
        reg = ToolRegistry()
        reg.register("sensitive", "Sensitive tool", {}, lambda: None)
        reg.mark_requires_approval("sensitive")
        assert reg._tools["sensitive"]["requires_approval"] is True

    def test_mark_requires_approval_unknown(self):
        reg = ToolRegistry()
        # Should not raise
        reg.mark_requires_approval("nonexistent")

    def test_execute_returns_json_string(self):
        reg = ToolRegistry()
        reg.register("list", "List", {}, lambda: [1, 2, 3])
        result = reg.execute("list", {})
        assert isinstance(result, str)
        assert json.loads(result) == [1, 2, 3]


class TestStreamAndCollectToolCalls:
    def _make_sse_chunk(self, delta: dict, finish_reason=None):
        choice = {"delta": delta}
        if finish_reason:
            choice["finish_reason"] = finish_reason
        return f"data: {json.dumps({'choices': [choice]})}\n\n".encode()

    def _make_done(self):
        return b"data: [DONE]\n\n"

    def _mock_response(self, lines: list):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = lines
        return mock_resp

    def test_collects_content(self):
        lines = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            b'data: {"choices": [{"delta": {"content": " World"}}]}',
            b'data: [DONE]',
        ]
        with patch("requests.post") as mock_post:
            mock_post.return_value = self._mock_response(lines)
            content, tool_calls, thinking, error = stream_and_collect_tool_calls(
                "https://api.example.com/chat", {}, {}
            )
        assert content == "Hello World"
        assert tool_calls == []
        assert error is None

    def test_collects_tool_calls(self):
        lines = [
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "search", "arguments": "{\\"kw\\""}}]}}]}',
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": \\"test\\"}"}}]}}]}',
            b'data: [DONE]',
        ]
        with patch("requests.post") as mock_post:
            mock_post.return_value = self._mock_response(lines)
            content, tool_calls, thinking, error = stream_and_collect_tool_calls(
                "https://api.example.com/chat", {}, {}
            )
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "search"
        assert tool_calls[0]["arguments"] == {"kw": "test"}

    def test_handles_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Unauthorized"}}
        with patch("requests.post") as mock_post:
            mock_post.return_value = mock_resp
            content, tool_calls, thinking, error = stream_and_collect_tool_calls(
                "https://api.example.com/chat", {}, {}
            )
        assert error is not None
        assert "401" in error or "Unauthorized" in error

    def test_handles_connection_error(self):
        import requests as req
        with patch("requests.post", side_effect=req.exceptions.ConnectionError("refused")):
            content, tool_calls, thinking, error = stream_and_collect_tool_calls(
                "https://api.example.com/chat", {}, {}
            )
        assert error is not None


class TestRunAgentLoop:
    def _mock_final_answer(self, text="Final answer"):
        """Mock that returns a direct content response (no tool calls)."""
        lines = [
            f'data: {{"choices": [{{"delta": {{"content": "{text}"}}}}]}}'.encode(),
            b'data: [DONE]',
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = lines
        return mock_resp

    def _mock_tool_call_then_answer(self, tool_name, args_json, answer):
        """Mock that first returns a tool call, then a final answer."""
        iter_count = [0]
        responses = [
            # Turn 1: tool call
            [
                f'data: {{"choices": [{{"delta": {{"tool_calls": [{{"index": 0, "id": "tc1", "function": {{"name": "{tool_name}", "arguments": "{args_json}"}}}}]}}}}]}}'.encode(),
                b'data: [DONE]',
            ],
            # Turn 2: final answer
            [
                f'data: {{"choices": [{{"delta": {{"content": "{answer}"}}}}]}}'.encode(),
                b'data: [DONE]',
            ],
        ]

        def post_side_effect(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = responses[min(iter_count[0], 1)]
            iter_count[0] += 1
            return mock_resp

        return post_side_effect

    def test_direct_answer_no_tools(self):
        reg = ToolRegistry()
        with patch("requests.post") as mock_post:
            mock_post.return_value = self._mock_final_answer("42")
            events = list(run_agent_loop("https://api.example.com", {}, {}, [], reg))
        contents = [json.loads(e[6:]) for e in events]
        content_event = next(c for c in contents if "content" in c)
        assert content_event["content"] == "42"
        assert any("done" in c for c in contents)

    def test_tool_call_and_answer(self):
        reg = ToolRegistry()
        reg.register("ping", "Ping", {"type": "object", "properties": {}}, lambda: "pong")

        with patch("requests.post") as mock_post:
            mock_post.side_effect = self._mock_tool_call_then_answer("ping", "{}", "Done!")
            events = list(run_agent_loop("https://api.example.com", {}, {}, [], reg))

        parsed = [json.loads(e[6:]) for e in events]
        tool_call_events = [p for p in parsed if "tool_call" in p]
        tool_result_events = [p for p in parsed if "tool_result" in p]
        content_events = [p for p in parsed if "content" in p]

        assert len(tool_call_events) == 1
        assert tool_call_events[0]["tool_call"]["name"] == "ping"
        assert len(tool_result_events) == 1
        assert len(content_events) == 1
        assert content_events[0]["content"] == "Done!"

    def test_max_iterations_guard(self):
        """Agent should give up after max_iterations."""
        reg = ToolRegistry()
        reg.register("loop", "Loop forever", {}, lambda: "keep going")

        call_count = [0]
        def always_tool_call(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = [
                f'data: {{"choices": [{{"delta": {{"tool_calls": [{{"index": 0, "id": "tc{call_count[0]}", "function": {{"name": "loop", "arguments": "{{}}"}}}}]}}}}]}}'.encode(),
                b'data: [DONE]',
            ]
            call_count[0] += 1
            return mock_resp

        with patch("requests.post", side_effect=always_tool_call):
            events = list(run_agent_loop("https://api.example.com", {}, {}, [], reg, max_iterations=2))

        parsed = [json.loads(e[6:]) for e in events]
        assert any("error" in p for p in parsed)
        assert call_count[0] <= 3  # max_iterations + possible final call

    def test_sse_format(self):
        """All yielded strings should be valid SSE format."""
        reg = ToolRegistry()
        with patch("requests.post") as mock_post:
            mock_post.return_value = self._mock_final_answer("hello")
            events = list(run_agent_loop("https://api.example.com", {}, {}, [], reg))

        for event in events:
            assert event.startswith("data: ")
            assert event.endswith("\n\n")
            json.loads(event[6:])  # must be valid JSON


class TestRunPlanAndExecuteLoop:
    def _mock_plan_then_answer(self, plan_text, answer):
        iter_count = [0]
        responses = [
            # Planning turn: returns a plan
            [f'data: {{"choices": [{{"delta": {{"content": "{plan_text}"}}}}]}}'.encode(), b'data: [DONE]'],
            # Execution turn: returns final answer (no tool calls)
            [f'data: {{"choices": [{{"delta": {{"content": "{answer}"}}}}]}}'.encode(), b'data: [DONE]'],
        ]

        def side_effect(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = responses[min(iter_count[0], 1)]
            iter_count[0] += 1
            return mock_resp

        return side_effect

    def test_plan_extracted_and_yielded(self):
        reg = ToolRegistry()
        plan = "PLAN:\\n1. Step one\\n2. Step two\\nEND_PLAN"
        with patch("requests.post", side_effect=self._mock_plan_then_answer(plan, "Result")):
            events = list(run_plan_and_execute_loop("https://api.example.com", {}, {}, [], reg))

        parsed = [json.loads(e[6:]) for e in events]
        plan_events = [p for p in parsed if "plan_start" in p]
        assert len(plan_events) == 1
        assert len(plan_events[0]["plan_start"]["steps"]) == 2

    def test_fallback_when_no_plan_format(self):
        """When model doesn't output PLAN: format, falls back to normal agent loop."""
        reg = ToolRegistry()
        with patch("requests.post") as mock_post:
            mock_post.return_value.__enter__ = mock_post.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = [
                b'data: {"choices": [{"delta": {"content": "Direct answer"}}]}',
                b'data: [DONE]',
            ]
            mock_post.return_value = mock_resp
            events = list(run_plan_and_execute_loop("https://api.example.com", {}, {}, [], reg))

        parsed = [json.loads(e[6:]) for e in events]
        # No plan event, but must have done event
        assert any("done" in p for p in parsed)


# ──────────────────────────── ai_memory ───────────────────────────────


import ai_memory


class TestAiMemory:
    @pytest.fixture(autouse=True)
    def reset_memory(self, tmp_path):
        # Reset module state between tests
        ai_memory._memory.clear()
        ai_memory._MEMORY_FILE = str(tmp_path / "ai_memory.json")
        yield
        ai_memory._memory.clear()

    def test_init_creates_file_path(self, tmp_path):
        ai_memory.init(str(tmp_path))
        assert ai_memory._MEMORY_FILE == str(tmp_path / "ai_memory.json")

    def test_get_relevant_memory_empty(self):
        result = ai_memory.get_relevant_memory(["wxid_test"])
        assert result == ""

    def test_get_all_memories_empty(self):
        result = ai_memory.get_all_memories()
        assert result == {}

    def test_delete_single_contact(self):
        with ai_memory._lock:
            ai_memory._memory["wxid_a"] = [{"summary": "test", "timestamp": time.time(), "session_id": "s1"}]
            ai_memory._memory["wxid_b"] = [{"summary": "test2", "timestamp": time.time(), "session_id": "s2"}]

        ai_memory.delete_memory("wxid_a")

        with ai_memory._lock:
            assert "wxid_a" not in ai_memory._memory
            assert "wxid_b" in ai_memory._memory

    def test_delete_all_memories(self):
        with ai_memory._lock:
            ai_memory._memory["wxid_a"] = [{"summary": "x", "timestamp": 1, "session_id": "s1"}]
            ai_memory._memory["wxid_b"] = [{"summary": "y", "timestamp": 1, "session_id": "s2"}]

        ai_memory.delete_memory()
        assert ai_memory.get_all_memories() == {}

    def test_get_relevant_memory_with_entries(self):
        now = time.time()
        with ai_memory._lock:
            ai_memory._memory["wxid_test"] = [
                {"summary": "关注工作项目", "timestamp": now, "session_id": "s1"},
                {"summary": "讨论年终总结", "timestamp": now - 100, "session_id": "s2"},
            ]

        result = ai_memory.get_relevant_memory(["wxid_test"])
        assert "关注工作项目" in result
        assert "讨论年终总结" in result

    def test_get_relevant_memory_excludes_old(self):
        old_time = time.time() - (35 * 86400)  # 35 days ago
        with ai_memory._lock:
            ai_memory._memory["wxid_old"] = [
                {"summary": "very old memory", "timestamp": old_time, "session_id": "s1"},
            ]

        result = ai_memory.get_relevant_memory(["wxid_old"])
        assert result == ""

    def test_persist_and_reload(self, tmp_path):
        ai_memory.init(str(tmp_path))
        now = time.time()
        with ai_memory._lock:
            ai_memory._memory["wxid_persist"] = [
                {"summary": "persisted", "timestamp": now, "session_id": "sess1"}
            ]
        ai_memory._save()

        # Reset and reload
        ai_memory._memory.clear()
        ai_memory._load()
        memories = ai_memory.get_all_memories()
        assert "wxid_persist" in memories
        assert memories["wxid_persist"][0]["summary"] == "persisted"


# ──────────────────────────── ai_scheduler ────────────────────────────


import ai_scheduler


class TestSchedulerMath:
    def test_daily_schedule(self):
        # "daily@10:30" should fire tomorrow at 10:30 if after that time today
        import datetime
        # Use a fixed reference time (noon)
        ref = datetime.datetime(2024, 1, 15, 12, 0, 0).timestamp()
        next_ts = ai_scheduler._compute_next_run("daily@10:30", ref)
        next_dt = datetime.datetime.fromtimestamp(next_ts)
        assert next_dt.hour == 10
        assert next_dt.minute == 30
        # Should be tomorrow since 12:00 > 10:30
        assert next_dt.date() > datetime.datetime.fromtimestamp(ref).date()

    def test_daily_schedule_before_time(self):
        import datetime
        # 08:00 ref time, schedule for 10:30 — should be same day
        ref = datetime.datetime(2024, 1, 15, 8, 0, 0).timestamp()
        next_ts = ai_scheduler._compute_next_run("daily@10:30", ref)
        next_dt = datetime.datetime.fromtimestamp(next_ts)
        assert next_dt.hour == 10
        assert next_dt.minute == 30
        assert next_dt.date() == datetime.datetime.fromtimestamp(ref).date()

    def test_interval_schedule(self):
        ref = 1000000.0
        next_ts = ai_scheduler._compute_next_run("interval@2h", ref)
        assert abs(next_ts - (ref + 7200)) < 1  # 2 hours = 7200 seconds

    def test_on_sync_schedule_returns_inf(self):
        next_ts = ai_scheduler._compute_next_run("on_sync", time.time())
        assert next_ts == float("inf")

    def test_weekly_schedule(self):
        import datetime
        # Monday 2024-01-15, schedule for weekly@fri@10:00
        ref = datetime.datetime(2024, 1, 15, 12, 0, 0).timestamp()  # Monday noon
        next_ts = ai_scheduler._compute_next_run("weekly@fri@10:00", ref)
        next_dt = datetime.datetime.fromtimestamp(next_ts)
        assert next_dt.weekday() == 4  # Friday
        assert next_dt.hour == 10


class TestSchedulerEngine:
    @pytest.fixture
    def engine(self, tmp_path):
        ai_scheduler.TASK_FILE = str(tmp_path / "tasks.json")
        mock_router = MagicMock()
        mock_router._get_active_provider.return_value = None  # no API key
        mock_push = MagicMock()
        eng = ai_scheduler.SchedulerEngine(str(tmp_path), mock_router, mock_push)
        return eng

    def test_create_and_list(self, engine):
        task = engine.create_task("Test", "Do something", "daily@09:00", ["wxid_a"], True)
        assert task["name"] == "Test"
        assert task["prompt"] == "Do something"
        tasks = engine.list_tasks()
        assert len(tasks) == 1

    def test_update_task(self, engine):
        task = engine.create_task("T", "P", "daily@09:00")
        ok = engine.update_task(task["id"], {"name": "Updated"})
        assert ok
        updated = engine.get_task(task["id"])
        assert updated["name"] == "Updated"

    def test_update_nonexistent(self, engine):
        ok = engine.update_task("nonexistent", {"name": "X"})
        assert not ok

    def test_delete_task(self, engine):
        task = engine.create_task("T", "P", "daily@09:00")
        ok = engine.delete_task(task["id"])
        assert ok
        assert engine.get_task(task["id"]) is None

    def test_delete_nonexistent(self, engine):
        ok = engine.delete_task("nonexistent")
        assert not ok

    def test_persist_and_reload(self, tmp_path):
        ai_scheduler.TASK_FILE = str(tmp_path / "tasks.json")
        mock_router = MagicMock()
        mock_router._get_active_provider.return_value = None
        mock_push = MagicMock()

        engine1 = ai_scheduler.SchedulerEngine(str(tmp_path), mock_router, mock_push)
        engine1.create_task("Saved Task", "Prompt", "on_sync")

        engine2 = ai_scheduler.SchedulerEngine(str(tmp_path), mock_router, mock_push)
        tasks = engine2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Saved Task"

    def test_fire_task_skips_without_api_key(self, engine):
        task = engine.create_task("T", "P", "daily@09:00")
        task_obj = engine._tasks[task["id"]]
        engine._fire_task(task_obj)
        # Should complete without error (no API key = early return)
        assert True

    def test_run_now_returns_false_for_unknown(self, engine):
        ok = engine.run_now("nonexistent_id")
        assert not ok

    def test_run_now_returns_true_for_valid(self, engine):
        task = engine.create_task("T", "P", "daily@09:00")
        ok = engine.run_now(task["id"])
        assert ok


# ──────────────────────────── ai_custom_tools ─────────────────────────


import ai_custom_tools


class TestCustomTools:
    @pytest.fixture(autouse=True)
    def reset_tools(self, tmp_path):
        ai_custom_tools._tools_cache.clear()
        ai_custom_tools._TOOLS_FILE = str(tmp_path / "custom_tools.json")
        yield
        ai_custom_tools._tools_cache.clear()

    def test_create_tool(self):
        tool = ai_custom_tools.create_tool(
            "my_tool", "Does something",
            {"param": {"type": "string"}},
            "Hello {param}",
        )
        assert tool["name"] == "my_tool"
        assert "id" in tool

    def test_list_tools(self):
        ai_custom_tools.create_tool("t1", "Tool 1", {}, "")
        ai_custom_tools.create_tool("t2", "Tool 2", {}, "")
        tools = ai_custom_tools.list_tools()
        assert len(tools) == 2

    def test_delete_tool(self):
        tool = ai_custom_tools.create_tool("t", "T", {}, "")
        ok = ai_custom_tools.delete_tool(tool["id"])
        assert ok
        assert len(ai_custom_tools.list_tools()) == 0

    def test_delete_nonexistent(self):
        ok = ai_custom_tools.delete_tool("nonexistent")
        assert not ok

    def test_persist_and_reload(self, tmp_path):
        ai_custom_tools.init(str(tmp_path))
        ai_custom_tools.create_tool("persisted", "P", {}, "")
        ai_custom_tools._tools_cache.clear()
        ai_custom_tools._load()
        assert len(ai_custom_tools.list_tools()) == 1
        assert ai_custom_tools.list_tools()[0]["name"] == "persisted"

    def test_register_in_registry(self):
        ai_custom_tools.create_tool(
            "greet", "Greet user",
            {"name": {"type": "string", "description": "User name"}},
            "Hello {name}!",
        )
        reg = ToolRegistry()
        mock_router = MagicMock()
        mock_router._get_active_provider.return_value = None  # no LLM
        ai_custom_tools.register_in_registry(reg, mock_router)
        assert reg.has("greet")

    def test_tool_openai_schema(self):
        ai_custom_tools.create_tool(
            "calc", "Calculate",
            {"expression": {"type": "string", "description": "Math expression"}},
            "Calculate: {expression}",
        )
        reg = ToolRegistry()
        mock_router = MagicMock()
        mock_router._get_active_provider.return_value = None
        ai_custom_tools.register_in_registry(reg, mock_router)
        openai_tools = reg.to_openai_tools()
        assert len(openai_tools) == 1
        func = openai_tools[0]["function"]
        assert func["name"] == "calc"
        assert "expression" in func["parameters"]["properties"]


# ──────────────────────────── search_messages ─────────────────────────


class TestSearchMessages:
    def test_returns_empty_when_no_tables(self):
        """search_messages should return [] gracefully when contact has no messages."""
        with patch("app.find_message_table", return_value=[]):
            from app import search_messages
            result = search_messages("wxid_test", "keyword")
            assert result == []

    def test_deduplicates_across_shards(self):
        """Messages with same local_id from different shards should be deduped."""
        mock_row = {
            "local_id": 1,
            "local_type": 1,
            "real_sender_id": 0,
            "create_time": 1700000000,
            "message_content": b"test keyword message",
            "WCDB_CT_message_content": 0,
        }

        def mock_row_factory(d):
            m = MagicMock()
            m.__getitem__ = lambda self, k: d[k]
            return m

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([mock_row_factory(mock_row)]))
        mock_conn.execute.return_value = mock_cursor

        with patch("app.find_message_table", return_value=[
            ("/db1.db", "Msg_abc"),
            ("/db2.db", "Msg_abc"),  # same table in two shards
        ]), patch("app.get_db", return_value=mock_conn), \
           patch("app._get_self_sender_id", return_value=99), \
           patch("app.decode_message_content", return_value="test keyword message"), \
           patch("app._get_contact_display_name", return_value="Alice"):
            from app import search_messages
            results = search_messages("wxid_test", "keyword", limit=50)
            # Should be deduped: only 1 result despite 2 shards
            assert len(results) == 1


# ──────────────────────────── Flask API endpoints ─────────────────────


@pytest.fixture
def client(tmp_path):
    """Flask test client with all modules initialized."""
    import app as flask_app
    import ai_memory as _mem
    import ai_custom_tools as _ct
    import ai_scheduler as _sched

    # Patch all module-level globals to use tmp dirs
    _mem._memory.clear()
    _mem._MEMORY_FILE = str(tmp_path / "ai_memory.json")
    _ct._tools_cache.clear()
    _ct._TOOLS_FILE = str(tmp_path / "custom_tools.json")
    ai_scheduler.TASK_FILE = str(tmp_path / "tasks.json")

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


class TestToolApprovalEndpoint:
    def test_approve_unknown_id(self, client):
        resp = client.post("/api/ai/tool_approval",
                           json={"id": "nonexistent", "approved": True},
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_approve_resolves_event(self, client):
        import app as flask_app
        ev = threading.Event()
        result = [False]
        flask_app._pending_approvals["test_id"] = (ev, result)

        resp = client.post("/api/ai/tool_approval",
                           json={"id": "test_id", "approved": True},
                           content_type="application/json")
        assert resp.status_code == 200
        assert ev.is_set()
        assert result[0] is True

    def test_deny_resolves_event(self, client):
        import app as flask_app
        ev = threading.Event()
        result = [True]
        flask_app._pending_approvals["deny_id"] = (ev, result)

        resp = client.post("/api/ai/tool_approval",
                           json={"id": "deny_id", "approved": False},
                           content_type="application/json")
        assert resp.status_code == 200
        assert ev.is_set()
        assert result[0] is False


class TestAgentTasksEndpoints:
    def test_list_tasks_empty(self, client):
        resp = client.get("/api/agent/tasks")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_task(self, client):
        resp = client.post("/api/agent/tasks",
                           json={"name": "Test Task", "prompt": "Do something", "schedule": "daily@09:00"},
                           content_type="application/json")
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Test Task"
        assert "id" in data

    def test_create_task_missing_name(self, client):
        resp = client.post("/api/agent/tasks",
                           json={"prompt": "Do something"},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_delete_task(self, client):
        create_resp = client.post("/api/agent/tasks",
                                  json={"name": "T", "prompt": "P", "schedule": "on_sync"},
                                  content_type="application/json")
        task_id = create_resp.get_json()["id"]
        del_resp = client.delete(f"/api/agent/tasks/{task_id}")
        assert del_resp.status_code == 200

    def test_delete_nonexistent_task(self, client):
        resp = client.delete("/api/agent/tasks/nonexistent_id")
        assert resp.status_code == 404

    def test_task_history(self, client):
        create_resp = client.post("/api/agent/tasks",
                                  json={"name": "T", "prompt": "P", "schedule": "on_sync"},
                                  content_type="application/json")
        task_id = create_resp.get_json()["id"]
        hist_resp = client.get(f"/api/agent/tasks/{task_id}/history")
        assert hist_resp.status_code == 200
        assert "history" in hist_resp.get_json()


class TestCustomToolsEndpoints:
    def test_list_empty(self, client):
        resp = client.get("/api/agent/custom_tools")
        assert resp.status_code == 200

    def test_create_and_list(self, client):
        resp = client.post("/api/agent/custom_tools",
                           json={"name": "my_tool", "description": "Does things", "parameters": {}, "prompt_template": ""},
                           content_type="application/json")
        assert resp.status_code == 201
        tool_id = resp.get_json()["id"]

        list_resp = client.get("/api/agent/custom_tools")
        tools = list_resp.get_json()
        assert any(t["id"] == tool_id for t in tools)

    def test_create_missing_fields(self, client):
        resp = client.post("/api/agent/custom_tools",
                           json={"name": "no_desc"},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_delete(self, client):
        create_resp = client.post("/api/agent/custom_tools",
                                  json={"name": "t", "description": "d"},
                                  content_type="application/json")
        tool_id = create_resp.get_json()["id"]
        del_resp = client.delete(f"/api/agent/custom_tools/{tool_id}")
        assert del_resp.status_code == 200

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/agent/custom_tools/nonexistent")
        assert resp.status_code == 404


class TestMemoryEndpoints:
    def test_get_memory_empty(self, client):
        resp = client.get("/api/ai/memory")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_delete_all_memory(self, client):
        resp = client.delete("/api/ai/memory",
                             json={},
                             content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_delete_contact_memory(self, client):
        resp = client.delete("/api/ai/memory",
                             json={"username": "wxid_test"},
                             content_type="application/json")
        assert resp.status_code == 200


class TestAgentPage:
    def test_agent_page_renders(self, client):
        resp = client.get("/agent")
        assert resp.status_code == 200
        assert b"Agent" in resp.data
