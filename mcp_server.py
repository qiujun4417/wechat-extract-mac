"""WeChat MCP Server — expose WeChat data tools via MCP stdio transport.

Run: python mcp_server.py
Then configure in Claude Desktop or Claude Code .claude/settings.json:

  {
    "mcpServers": {
      "wechat": {
        "command": "python",
        "args": ["/path/to/wechat-extract-mac/mcp_server.py"]
      }
    }
  }

Exposes 5 tools:
  - search_messages(username, keyword, limit)
  - get_messages_in_timerange(username, time_from, time_to, limit)
  - get_contact_stats(username)
  - get_contacts_list(include_groups, include_official, keyword)
  - get_message_count(username)
"""

import json
import sys
import os

# Add project dir to path so we can import app data functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
except ImportError:
    print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

import app as wechat_app

server = Server("wechat-extract")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_messages",
            description="在指定联系人的聊天记录中全文搜索关键词。返回匹配消息列表。",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "联系人微信 ID (wxid)"},
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 20},
                },
                "required": ["username", "keyword"],
            },
        ),
        types.Tool(
            name="get_messages_in_timerange",
            description="获取指定联系人在某时间段内的聊天消息。time_from/time_to 为 Unix 时间戳。",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "联系人微信 ID"},
                    "time_from": {"type": "integer", "description": "开始时间戳（秒）", "default": 0},
                    "time_to": {"type": "integer", "description": "结束时间戳（秒）", "default": 0},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 100},
                },
                "required": ["username"],
            },
        ),
        types.Tool(
            name="get_contact_stats",
            description="获取联系人的消息统计：总消息数、最早/最新消息日期。",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "联系人微信 ID"},
                },
                "required": ["username"],
            },
        ),
        types.Tool(
            name="get_contacts_list",
            description="获取微信联系人列表。可按名称过滤，可选择是否包含群聊和公众号。",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_groups": {"type": "boolean", "description": "是否包含群聊", "default": True},
                    "include_official": {"type": "boolean", "description": "是否包含公众号", "default": False},
                    "keyword": {"type": "string", "description": "按名称过滤", "default": ""},
                },
            },
        ),
        types.Tool(
            name="get_message_count",
            description="获取指定联系人的总消息数量。",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "联系人微信 ID"},
                },
                "required": ["username"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "search_messages":
            result = wechat_app.search_messages(
                arguments["username"],
                arguments["keyword"],
                int(arguments.get("limit", 20)),
            )
        elif name == "get_messages_in_timerange":
            result = wechat_app.get_messages_by_timerange(
                arguments["username"],
                int(arguments.get("time_from", 0)),
                int(arguments.get("time_to", 0)),
                int(arguments.get("limit", 100)),
            )
        elif name == "get_contact_stats":
            from ai_agent import _get_contact_stats
            result = _get_contact_stats(arguments["username"])
        elif name == "get_contacts_list":
            from ai_agent import _get_contacts_list
            result = _get_contacts_list(
                arguments.get("include_groups", True),
                arguments.get("include_official", False),
                arguments.get("keyword", ""),
            )
        elif name == "get_message_count":
            result = {"username": arguments["username"], "count": wechat_app.get_message_count(arguments["username"])}
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
