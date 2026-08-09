#!/usr/bin/env python3
"""WeChat Mac Chat History Export - Flask Web Application"""

import sqlite3
import os
import sys
import hashlib
import subprocess
import json
import zlib
import time
import csv
import io
import zipfile
from datetime import datetime
import html as html_lib
from flask import (
    Flask, render_template, jsonify, request, send_file, Response, stream_with_context
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Paths
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_BASE = os.path.join(PROJECT_DIR, "decrypted")
KEYS_FILE = os.path.join(PROJECT_DIR, "all_keys.json")
CONTACTS_CACHE_FILE = os.path.join(PROJECT_DIR, "contacts_cache.json")
PASSPHRASE_FILE = os.path.expanduser("~/.wcdb-key-tool/wechat-passphrase.json")
WCDB_TOOL = os.path.join(PROJECT_DIR, "decrypt_core.py")
WECHAT_DATA_BASE = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
CONTACT_DB = os.path.join(DB_BASE, "contact", "contact.db")

# Dynamically find all message DBs (WeChat adds new shards over time: message_0..N)
def _discover_message_dbs():
    msg_dir = os.path.join(DB_BASE, "message")
    message_dbs = []
    biz_message_dbs = []
    if os.path.exists(msg_dir):
        for f in sorted(os.listdir(msg_dir)):
            full = os.path.join(msg_dir, f)
            if f.startswith("message_") and f.endswith(".db") and "fts" not in f and "resource" not in f and "kvdb" not in f:
                message_dbs.append(full)
            elif f.startswith("biz_message_") and f.endswith(".db") and "kvdb" not in f:
                biz_message_dbs.append(full)
    return message_dbs, biz_message_dbs

MESSAGE_DBS, BIZ_MESSAGE_DBS = _discover_message_dbs()


def check_setup_status():
    """Check the current setup status and return state info."""
    status = {
        "wechat_installed": False,
        "wechat_running": False,
        "wechat_signed": False,
        "databases_decrypted": False,
        "tool_available": False,
        "step": 0,  # Current step user needs to do
    }

    # Check if WeChat is installed
    status["wechat_installed"] = os.path.exists("/Applications/WeChat.app")

    # Check if WeChat data exists
    if os.path.exists(WECHAT_DATA_BASE):
        user_dirs = [
            d for d in os.listdir(WECHAT_DATA_BASE)
            if os.path.isdir(os.path.join(WECHAT_DATA_BASE, d))
            and d not in ("all_users", "Backup")
        ]
        status["has_user_data"] = len(user_dirs) > 0
    else:
        status["has_user_data"] = False

    # Check if WeChat is running
    import subprocess
    result = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True)
    status["wechat_running"] = result.returncode == 0

    # Check if tool is available
    status["tool_available"] = os.path.exists(WCDB_TOOL)

    # Check if databases are already decrypted
    status["databases_decrypted"] = (
        os.path.exists(CONTACT_DB) and
        os.path.exists(os.path.join(DB_BASE, "message"))
    )

    # Check if passphrase is cached (can skip lldb step)
    status["has_passphrase"] = os.path.exists(PASSPHRASE_FILE)

    # Check if keys file exists (can skip extraction entirely, just decrypt)
    status["has_keys"] = os.path.exists(KEYS_FILE)

    # Check if WeChat is re-signed (no hardened runtime)
    if status["wechat_installed"]:
        try:
            r = subprocess.run(
                ["codesign", "-dv", "/Applications/WeChat.app/Contents/MacOS/WeChat"],
                capture_output=True, text=True, timeout=5
            )
            # If signed with Apple developer cert, it has hardened runtime
            status["wechat_signed"] = "runtime" not in r.stderr
        except Exception:
            status["wechat_signed"] = False

    # Determine which step user is on
    if status["databases_decrypted"]:
        status["step"] = 99  # All done
    elif not status["wechat_installed"]:
        status["step"] = 0
    elif not status["wechat_signed"]:
        status["step"] = 1  # Need to re-sign
    elif not status["has_passphrase"] and not status["has_keys"]:
        status["step"] = 2  # Need to capture passphrase (lldb)
    elif status["has_passphrase"] or status["has_keys"]:
        status["step"] = 3  # Has keys/passphrase, just need to decrypt
    else:
        status["step"] = 2

    return status


def get_db(path):
    """Open a database in immutable read-only mode."""
    uri = f"file:{path}?immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def md5_hash(s):
    """Get MD5 hash of a string."""
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def decode_message_content(content, ct_type):
    """Decode message content based on WCDB_CT type."""
    if content is None:
        return ""
    if ct_type == 4:
        try:
            if isinstance(content, str):
                content = content.encode("utf-8")
            # Check for zstandard magic bytes
            if content[:4] == b'\x28\xb5\x2f\xfd':
                try:
                    import zstandard as zstd
                    d = zstd.ZstdDecompressor()
                    return d.decompress(content).decode("utf-8", errors="replace")
                except Exception:
                    pass
            # Fall back to zlib
            return zlib.decompress(content).decode("utf-8", errors="replace")
        except Exception:
            return str(content)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content) if content else ""


def get_base_msg_type(local_type):
    """Extract the base message type from local_type (low 16 bits)."""
    return local_type & 0xFFFF


def get_message_type_label(local_type):
    """Return a human-readable label for message types."""
    base = get_base_msg_type(local_type)
    types = {
        1: "text",
        3: "[Image]",
        34: "[Voice]",
        42: "[Contact Card]",
        43: "[Video]",
        47: "[Sticker]",
        48: "[Location]",
        49: "[Link/File]",
        50: "[Voice/Video Call]",
        10000: "[System]",
        10002: "[System]",
    }
    return types.get(base, f"[Type:{local_type}]")


def get_all_contacts():
    """Load all contacts from the contact database."""
    if not os.path.exists(CONTACT_DB):
        return []
    conn = get_db(CONTACT_DB)
    try:
        cursor = conn.execute(
            "SELECT username, nick_name, remark, alias, local_type, "
            "small_head_url, big_head_url FROM contact"
        )
        contacts = []
        for row in cursor:
            contacts.append({
                "username": row["username"],
                "nick_name": row["nick_name"] or "",
                "remark": row["remark"] or "",
                "alias": row["alias"] or "",
                "local_type": row["local_type"],
                "avatar": row["small_head_url"] or "",
                "is_group": "@chatroom" in (row["username"] or ""),
                "is_official": (row["username"] or "").startswith("gh_"),
            })
        return contacts
    finally:
        conn.close()


# Cache for table-to-db mapping and stats
_table_index_cache = None  # {table_name: db_path}
_stats_cache = None  # {table_name: {count, last_time}}


def _build_table_index():
    """Build a mapping of Msg_<hash> table names to their DB paths."""
    global _table_index_cache
    if _table_index_cache is not None:
        return _table_index_cache
    _table_index_cache = {}
    for db_path in MESSAGE_DBS + BIZ_MESSAGE_DBS:
        if not os.path.exists(db_path):
            continue
        try:
            conn = get_db(db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            )
            for row in cursor:
                _table_index_cache.setdefault(row["name"], []).append(db_path)
            conn.close()
        except Exception:
            continue
    return _table_index_cache


# Cache for self sender_id per DB
_self_sender_id_cache = {}  # {db_path: sender_id}
SELF_USERNAME = "skaterboy03"  # The current user WeChat username


def _get_self_sender_id(db_path):
    """Get the rowid of the current user in a message DB Name2Id table."""
    if db_path in _self_sender_id_cache:
        return _self_sender_id_cache[db_path]
    try:
        conn = get_db(db_path)
        cursor = conn.execute("SELECT rowid FROM Name2Id WHERE user_name=?", (SELF_USERNAME,))
        row = cursor.fetchone()
        conn.close()
        if row:
            _self_sender_id_cache[db_path] = row[0]
            return row[0]
    except Exception:
        pass
    _self_sender_id_cache[db_path] = 0
    return 0


# Cache for contact name resolution
_contact_name_cache = None  # {username: display_name}


def _build_contact_name_cache():
    """Build a cache of username -> display_name from contact DB."""
    global _contact_name_cache
    if _contact_name_cache is not None:
        return _contact_name_cache
    _contact_name_cache = {}
    if not os.path.exists(CONTACT_DB):
        return _contact_name_cache
    try:
        conn = get_db(CONTACT_DB)
        cursor = conn.execute("SELECT username, nick_name, remark FROM contact")
        for row in cursor:
            name = row["remark"] or row["nick_name"] or row["username"]
            _contact_name_cache[row["username"]] = name
        conn.close()
    except Exception:
        pass
    return _contact_name_cache


def _resolve_sender_name(wxid):
    """Resolve a wxid to display name (remark > nickname > wxid)."""
    cache = _build_contact_name_cache()
    return cache.get(wxid, wxid)


def _get_contact_display_name(username):
    """Get display name for a contact."""
    cache = _build_contact_name_cache()
    return cache.get(username, username)


def find_message_table(username):
    """Find which message DBs contain messages for a given username.
    Returns list of (db_path, table_name) tuples."""
    table_name = f"Msg_{md5_hash(username)}"
    index = _build_table_index()
    db_paths = index.get(table_name, [])
    if db_paths:
        return [(p, table_name) for p in db_paths]
    return []


def _build_stats_cache():
    """Build message count and last_time for ALL message tables at once."""
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    _stats_cache = {}
    index = _build_table_index()

    # Group tables by db_path (index now maps table_name -> [db_paths])
    db_tables = {}
    for table_name, db_paths in index.items():
        for db_path in db_paths:
            db_tables.setdefault(db_path, []).append(table_name)

    for db_path, tables in db_tables.items():
        try:
            conn = get_db(db_path)
            for table_name in tables:
                try:
                    cursor = conn.execute(
                        f"SELECT COUNT(*) as cnt, MAX(create_time) as last_time "
                        f"FROM [{table_name}]"
                    )
                    row = cursor.fetchone()
                    if row and row["cnt"]:
                        # Accumulate across multiple DBs
                        if table_name not in _stats_cache:
                            _stats_cache[table_name] = {"count": 0, "last_time": 0}
                        _stats_cache[table_name]["count"] += row["cnt"]
                        _stats_cache[table_name]["last_time"] = max(
                            _stats_cache[table_name]["last_time"], row["last_time"] or 0
                        )
                except Exception:
                    continue
            conn.close()
        except Exception:
            continue
    return _stats_cache


def get_message_count(username):
    """Get message count for a username."""
    table_name = f"Msg_{md5_hash(username)}"
    stats = _build_stats_cache()
    entry = stats.get(table_name)
    return entry["count"] if entry else 0


def get_last_message_time(username):
    """Get the last message timestamp for a username."""
    table_name = f"Msg_{md5_hash(username)}"
    stats = _build_stats_cache()
    entry = stats.get(table_name)
    return entry["last_time"] if entry else 0


def get_messages(username, limit=50, offset=0, skip_images=False):
    """Get messages for a username across ALL shards. skip_images=True for AI context (faster)."""
    tables = find_message_table(username)
    if not tables:
        return []

    # Collect messages from all DBs, sorted by create_time DESC, then apply offset/limit
    all_raw = []
    for db_path, table_name in tables:
        try:
            conn = get_db(db_path)
            cursor = conn.execute(
                f"SELECT local_id, local_type, sort_seq, real_sender_id, "
                f"create_time, message_content, WCDB_CT_message_content "
                f"FROM [{table_name}] ORDER BY create_time DESC",
            )
            for row in cursor:
                all_raw.append((db_path, dict(row)))
            conn.close()
        except Exception:
            continue

    # Sort all by create_time DESC (newest first)
    all_raw.sort(key=lambda x: x[1]["create_time"] or 0, reverse=True)

    # Apply offset and limit
    page = all_raw[offset:offset + limit]

    # Process the page
    messages = []
    for db_path, row in page:
        self_id = _get_self_sender_id(db_path)
        ct_type = row["WCDB_CT_message_content"] if row["WCDB_CT_message_content"] else 0
        content = decode_message_content(row["message_content"], ct_type)
        local_type = row["local_type"]
        base_type = get_base_msg_type(local_type)
        image_url = None

        if base_type != 1:
            display = get_message_type_label(local_type)
            if base_type == 49 and content:
                import re
                # Check if it's an mmreader (news feed) message with multiple items
                if "<mmreader>" in content:
                    items = re.findall(
                        r'<(?:item|newitem)>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>.*?<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>.*?</(?:item|newitem)>',
                        content, re.DOTALL
                    )
                    if items:
                        links = []
                        for title, url in items:
                            if url and url.startswith("http"):
                                links.append(f'<a href="{html_lib.escape(url)}" target="_blank" class="msg-link">🔗 {html_lib.escape(title)}</a>')
                            else:
                                links.append(f"📰 {title}")
                        display = "<br>".join(links)
                    else:
                        display = "[News Feed]"
                else:
                    # Standard appmsg with single title/url
                    title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", content)
                    url_match = re.search(r"<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>", content)
                    if title_match:
                        title_text = title_match.group(1)
                        link_url = url_match.group(1) if url_match else ""
                        if link_url and link_url.startswith("http"):
                            display = f'<a href="{html_lib.escape(link_url)}" target="_blank" class="msg-link">🔗 {html_lib.escape(title_text)}</a>'
                        else:
                            display = f"[Link] {title_text}"
            if base_type == 3 and row["create_time"] and not skip_images:
                img_file = _find_image_for_message(username, row["create_time"])
                if img_file:
                    image_url = f"/api/image/{username}/{img_file}"
        else:
            display = content
            # Some text messages contain XML (e.g. mmreader news feeds from Tencent News)
            if isinstance(display, str) and "<mmreader>" in display:
                import re
                items = re.findall(
                    r'<(?:item|newitem)>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>.*?<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>.*?</(?:item|newitem)>',
                    display, re.DOTALL
                )
                if items:
                    links = []
                    for title, url in items:
                        if url and url.startswith("http"):
                            links.append(f'<a href="{html_lib.escape(url)}" target="_blank" class="msg-link">🔗 {html_lib.escape(title)}</a>')
                        else:
                            links.append(f"📰 {title}")
                    display = "<br>".join(links)
                else:
                    display = "[News Feed]"

        # Resolve sender name and strip wxid prefix from group messages
        sender_name = ""
        is_self = row["real_sender_id"] == self_id
        if is_self:
            sender_name = "我"
        elif "@chatroom" in username and isinstance(display, str) and ":\n" in display:
            parts = display.split(":\n", 1)
            sender_wxid = parts[0]
            display = parts[1] if len(parts) > 1 else display
            sender_name = _resolve_sender_name(sender_wxid)
        elif "@chatroom" not in username:
            sender_name = _get_contact_display_name(username)

        msg_data = {
            "id": row["local_id"],
            "type": local_type,
            "base_type": base_type,
            "time": row["create_time"],
            "time_str": datetime.fromtimestamp(
                row["create_time"]
            ).strftime("%Y-%m-%d %H:%M:%S") if row["create_time"] else "",
            "is_self": is_self,
            "content": display,
            "sender_name": sender_name,
            "sender_id": row["real_sender_id"],
        }
        if image_url:
            msg_data["image_url"] = image_url
        messages.append(msg_data)

    messages.reverse()  # Return in chronological order (oldest first)
    return messages


def get_messages_by_timerange(username, time_from=0, time_to=0, limit=200):
    """Get messages within a time range across all shards."""
    tables = find_message_table(username)
    if not tables:
        return []

    all_raw = []
    for db_path, table_name in tables:
        try:
            self_id = _get_self_sender_id(db_path)
            conn = get_db(db_path)
            # Build WHERE clause for time range
            conditions = []
            params = []
            if time_from:
                conditions.append("create_time >= ?")
                params.append(time_from)
            if time_to:
                conditions.append("create_time <= ?")
                params.append(time_to)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor = conn.execute(
                f"SELECT local_id, local_type, sort_seq, real_sender_id, "
                f"create_time, message_content, WCDB_CT_message_content "
                f"FROM [{table_name}] {where} ORDER BY create_time ASC",
                params
            )
            for row in cursor:
                all_raw.append((db_path, self_id, dict(row)))
            conn.close()
        except Exception:
            continue

    # Sort by time
    all_raw.sort(key=lambda x: x[2]["create_time"] or 0)

    # Limit
    if len(all_raw) > limit:
        # Sample evenly across the time range
        step = len(all_raw) / limit
        sampled = [all_raw[int(i * step)] for i in range(limit)]
        all_raw = sampled

    # Process
    messages = []
    for db_path, self_id, row in all_raw:
        ct_type = row["WCDB_CT_message_content"] if row["WCDB_CT_message_content"] else 0
        content = decode_message_content(row["message_content"], ct_type)
        local_type = row["local_type"]
        base_type = get_base_msg_type(local_type)

        if base_type != 1:
            display = get_message_type_label(local_type)
        else:
            display = content

        # Resolve sender
        sender_name = ""
        is_self = row["real_sender_id"] == self_id
        if is_self:
            sender_name = "我"
        elif "@chatroom" in username and isinstance(display, str) and ":\n" in display:
            parts = display.split(":\n", 1)
            sender_wxid = parts[0]
            display = parts[1] if len(parts) > 1 else display
            sender_name = _resolve_sender_name(sender_wxid)
        elif "@chatroom" not in username:
            sender_name = _get_contact_display_name(username)

        messages.append({
            "time": row["create_time"],
            "time_str": datetime.fromtimestamp(row["create_time"]).strftime("%Y-%m-%d %H:%M:%S") if row["create_time"] else "",
            "is_self": is_self,
            "content": display,
            "sender_name": sender_name,
        })

    return messages


def get_all_messages(username):
    """Get ALL messages for a username across all shards (for export)."""
    tables = find_message_table(username)
    if not tables:
        return []

    all_messages = []
    for db_path, table_name in tables:
        try:
            self_id = _get_self_sender_id(db_path)
            conn = get_db(db_path)
            cursor = conn.execute(
                f"SELECT local_id, local_type, sort_seq, real_sender_id, "
                f"create_time, message_content, WCDB_CT_message_content "
                f"FROM [{table_name}] ORDER BY create_time ASC"
            )
            for row in cursor:
                ct_type = row["WCDB_CT_message_content"] if row["WCDB_CT_message_content"] else 0
                content = decode_message_content(row["message_content"], ct_type)
                local_type = row["local_type"]
                if local_type != 1:
                    display = get_message_type_label(local_type)
                    if local_type == 49 and content:
                        import re
                        title_match = re.search(r"<title>(.*?)</title>", content)
                        if title_match:
                            display = f"[Link] {title_match.group(1)}"
                else:
                    display = content

                all_messages.append({
                    "id": row["local_id"],
                    "type": local_type,
                    "time": row["create_time"],
                    "time_str": datetime.fromtimestamp(
                        row["create_time"]
                    ).strftime("%Y-%m-%d %H:%M:%S") if row["create_time"] else "",
                    "is_self": row["real_sender_id"] == self_id,
                    "content": display,
                    "sender_id": row["real_sender_id"],
                })
            conn.close()
        except Exception:
            continue

    # Sort all messages by time
    all_messages.sort(key=lambda m: m["time"] or 0)
    return all_messages


@app.route("/")
def index():
    status = check_setup_status()
    if status["step"] != 99:
        return render_template("setup.html", status=status)
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Return current setup status."""
    return jsonify(check_setup_status())


@app.route("/api/decrypt/resign", methods=["POST"])
def api_resign():
    """Re-sign WeChat to remove hardened runtime. Requires sudo (run app with sudo)."""
    try:
        result = subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", "/Applications/WeChat.app"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": "重签名成功！请重启微信。"})
        else:
            return jsonify({"success": False, "message": f"重签名失败: {result.stderr}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "重签名超时"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/decrypt/capture")
def api_capture_passphrase():
    """Start LLDB passphrase capture. Streams progress via SSE."""
    import decrypt_core

    def generate():
        yield f"data: {json.dumps({'type': 'info', 'message': '正在附加到微信进程...'})}\n\n"

        pid = decrypt_core._find_wechat_pid()
        if not pid:
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到微信进程，请先启动微信'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'info', 'message': f'已找到微信进程 (PID: {pid})，设置断点中...'})}\n\n"
        yield f"data: {json.dumps({'type': 'action', 'message': '请在微信中退出登录，然后重新登录（触发密钥计算）'})}\n\n"

        try:
            passphrase_hex = decrypt_core.capture_passphrase_lldb(timeout=180)
            decrypt_core.save_passphrase(passphrase_hex)
            yield f"data: {json.dumps({'type': 'success', 'message': f'Passphrase 捕获成功！'})}\n\n"
        except decrypt_core.CaptureError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'捕获失败: {str(e)}'})}\n\n"
            return
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'未知错误: {str(e)}'})}\n\n"
            return

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/decrypt/run")
def api_decrypt_run():
    """Run the full decrypt pipeline (derive keys + decrypt DBs). Streams progress via SSE."""
    import decrypt_core

    def generate():
        # Step 1: Detect DB directory
        db_dir = decrypt_core.auto_detect_db_dir()
        if not db_dir:
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到微信数据库目录'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'info', 'message': f'数据库目录: {os.path.basename(os.path.dirname(db_dir))}'})}\n\n"

        # Step 2: Collect DB files
        db_files, salt_to_dbs = decrypt_core.collect_db_files(db_dir)
        if not db_files:
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到可解密的数据库文件'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'info', 'message': f'找到 {len(db_files)} 个数据库, {len(salt_to_dbs)} 个不同的密钥'})}\n\n"

        # Step 3: Get passphrase
        passphrase_hex = decrypt_core.load_passphrase()
        if not passphrase_hex:
            yield f"data: {json.dumps({'type': 'error', 'message': '没有可用的 passphrase，请先执行密钥捕获步骤'})}\n\n"
            return

        # Step 4: Derive keys via PBKDF2
        yield f"data: {json.dumps({'type': 'info', 'message': '开始 PBKDF2 密钥派生（约30-60秒）...'})}\n\n"
        key_map = {}
        total = len(salt_to_dbs)
        passphrase = bytes.fromhex(passphrase_hex)

        for i, salt_hex in enumerate(salt_to_dbs):
            salt = bytes.fromhex(salt_hex)
            enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)
            for _rel, _path, _sz, s, page1 in db_files:
                if s == salt_hex and decrypt_core.verify_enc_key(enc_key, page1):
                    key_map[salt_hex] = enc_key.hex()
                    break
            if (i + 1) % 3 == 0 or i == total - 1:
                yield f"data: {json.dumps({'type': 'progress', 'message': f'密钥派生: {i+1}/{total} ({len(key_map)} 验证通过)', 'progress': (i+1)/total*50})}\n\n"

        if not key_map:
            yield f"data: {json.dumps({'type': 'error', 'message': 'PBKDF2 派生后未能验证任何密钥，passphrase 可能已失效'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'info', 'message': f'密钥派生完成: {len(key_map)}/{total} 成功'})}\n\n"

        # Save keys
        keys_data = {"_db_dir": db_dir}
        for rel, path, sz, salt, page1 in db_files:
            if salt in key_map:
                keys_data[rel] = {"enc_key": key_map[salt], "salt": salt}
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys_data, f, indent=2)

        # Step 5: Decrypt databases
        yield f"data: {json.dumps({'type': 'info', 'message': '开始解密数据库...'})}\n\n"
        out_dir = DB_BASE
        os.makedirs(out_dir, exist_ok=True)

        success = 0
        failed = 0
        decrypt_files = [(rel, path, sz) for rel, path, sz, salt, page1 in db_files]
        decrypt_files.sort(key=lambda x: x[2])

        for idx, (rel, path, sz) in enumerate(decrypt_files):
            with open(path, "rb") as f:
                page1 = f.read(decrypt_core.PAGE_SZ)
            salt_hex = page1[:decrypt_core.SALT_SZ].hex()
            if salt_hex not in key_map:
                continue

            enc_key = bytes.fromhex(key_map[salt_hex])
            out_path = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            try:
                if decrypt_core._decrypt_database(path, out_path, enc_key):
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            progress = 50 + (idx + 1) / len(decrypt_files) * 50
            yield f"data: {json.dumps({'type': 'progress', 'message': f'解密: {rel} ({sz//1024//1024}MB)', 'progress': progress})}\n\n"

        # Done!
        global _table_index_cache, _stats_cache, MESSAGE_DBS, BIZ_MESSAGE_DBS
        _table_index_cache = None
        _stats_cache = None
        MESSAGE_DBS, BIZ_MESSAGE_DBS = _discover_message_dbs()

        yield f"data: {json.dumps({'type': 'done', 'message': f'解密完成！成功 {success} 个，失败 {failed} 个', 'success': success, 'failed': failed})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# Track source DB modification times for lightweight change detection
_last_sync_mtimes = {}

# ============================================================
# Real-time Event Push (SSE long-poll)
# ============================================================

import threading
import queue

# Global list of subscriber queues for SSE push
_event_subscribers = []
_event_lock = threading.Lock()


def _push_event(event_data):
    """Push an event to all connected SSE subscribers."""
    with _event_lock:
        dead = []
        for q in _event_subscribers:
            try:
                q.put_nowait(event_data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _event_subscribers.remove(q)


def _rebuild_contacts_cache():
    """Rebuild the contacts JSON cache file after sync."""
    try:
        contacts = get_all_contacts()
        enriched = []
        for c in contacts:
            count = get_message_count(c["username"])
            if count == 0:
                continue
            last_time = get_last_message_time(c["username"])
            c["message_count"] = count
            c["last_active"] = (
                datetime.fromtimestamp(last_time).strftime("%Y-%m-%d")
                if last_time else ""
            )
            c["last_active_ts"] = last_time
            display_name = c["remark"] or c["nick_name"] or c["username"]
            c["display_name"] = display_name
            enriched.append(c)
        enriched.sort(key=lambda x: x["last_active_ts"], reverse=True)

        cache_data = {
            "updated_at": int(time.time()),
            "contacts": enriched
        }
        with open(CONTACTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
        print(f"[Cache] Contacts cache updated: {len(enriched)} contacts")
        return enriched
    except Exception as e:
        print(f"[Cache] Failed to rebuild contacts cache: {e}")
        return None


@app.route("/api/events")
def api_events():
    """SSE endpoint for real-time push notifications to the frontend.

    Clients connect once and receive events when:
    - contacts_updated: contacts list has changed
    - sync_complete: a sync operation finished (with new_messages count)
    """
    def generate():
        q = queue.Queue(maxsize=50)
        with _event_lock:
            _event_subscribers.append(q)
        try:
            # Send initial heartbeat
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    # Block for up to 30s, then send heartbeat
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # Send heartbeat comment to keep connection alive
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _event_lock:
                if q in _event_subscribers:
                    _event_subscribers.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@app.route("/api/decrypt/sync/auto")
def api_decrypt_sync_auto():
    """Lightweight auto-sync: only re-decrypt if source DB files have changed.

    Compares modification times of source WeChat DB files against last sync.
    If no changes detected, returns immediately (idempotent / no-op).
    This is safe to call every 10 seconds without duplicating data.
    """
    import decrypt_core
    global _last_sync_mtimes

    # Check if we have keys
    passphrase_hex = decrypt_core.load_passphrase()
    has_keys = os.path.exists(KEYS_FILE)
    if not passphrase_hex and not has_keys:
        def noop():
            yield f"data: {json.dumps({'type': 'done', 'message': 'no_keys'})}\n\n"
        return Response(stream_with_context(noop()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Detect DB directory
    db_dir = decrypt_core.auto_detect_db_dir()
    if not db_dir:
        def noop():
            yield f"data: {json.dumps({'type': 'done', 'message': 'no_db_dir'})}\n\n"
        return Response(stream_with_context(noop()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Check file modification times - only sync if something changed
    current_mtimes = {}
    changed = False
    try:
        for root, dirs, files in os.walk(db_dir):
            for f in files:
                if f.endswith(".db") or f.endswith(".db-wal"):
                    fpath = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fpath)
                        current_mtimes[fpath] = mtime
                        if fpath not in _last_sync_mtimes or _last_sync_mtimes[fpath] != mtime:
                            changed = True
                    except OSError:
                        continue
    except Exception:
        pass

    if not changed and _last_sync_mtimes:
        # No changes - return immediately (idempotent no-op)
        def noop():
            yield f"data: {json.dumps({'type': 'done', 'message': 'no_changes'})}\n\n"
        return Response(stream_with_context(noop()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Changes detected - update mtimes and proceed with sync
    _last_sync_mtimes = current_mtimes

    # Delegate to the full sync logic (same SSE streaming)
    return api_decrypt_sync()


@app.route("/api/decrypt/sync")
def api_decrypt_sync():
    """Re-decrypt databases to pick up new messages (incremental sync).

    Uses the saved passphrase/keys to re-decrypt all DBs from the live WeChat data.
    This is the same as /api/decrypt/run but framed as a sync operation.
    """
    import decrypt_core

    def generate():
        yield f"data: {json.dumps({'type': 'info', 'message': '🔄 开始同步新消息...'})}\n\n"

        # Check if we have passphrase or keys
        passphrase_hex = decrypt_core.load_passphrase()
        has_keys = os.path.exists(KEYS_FILE)

        if not passphrase_hex and not has_keys:
            yield f"data: {json.dumps({'type': 'error', 'message': '没有可用的密钥信息，请先完成首次解密'})}\n\n"
            return

        # Detect DB directory
        db_dir = decrypt_core.auto_detect_db_dir()
        if not db_dir:
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到微信数据库目录，请确保微信已登录'})}\n\n"
            return

        # Collect DB files
        db_files, salt_to_dbs = decrypt_core.collect_db_files(db_dir)
        if not db_files:
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到数据库文件'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'info', 'message': f'找到 {len(db_files)} 个数据库文件'})}\n\n"

        # Try to use existing keys first (fast path)
        key_map = {}
        if has_keys:
            try:
                with open(KEYS_FILE, "r", encoding="utf-8") as f:
                    saved_keys = json.load(f)
                # Validate existing keys against current DB files
                valid = 0
                for rel, path, sz, salt_hex, page1 in db_files:
                    for k, v in saved_keys.items():
                        if isinstance(v, dict) and v.get("salt") == salt_hex:
                            enc_key = bytes.fromhex(v["enc_key"])
                            if decrypt_core.verify_enc_key(enc_key, page1):
                                key_map[salt_hex] = v["enc_key"]
                                valid += 1
                                break
                if valid > 0:
                    yield f"data: {json.dumps({'type': 'info', 'message': f'已验证 {valid} 个已有密钥'})}\n\n"
            except Exception:
                pass

        # For any salts without keys, derive from passphrase
        remaining_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
        if remaining_salts and passphrase_hex:
            yield f"data: {json.dumps({'type': 'info', 'message': f'派生 {len(remaining_salts)} 个新密钥 (PBKDF2)...'})}\n\n"
            passphrase = bytes.fromhex(passphrase_hex)
            for i, salt_hex in enumerate(remaining_salts):
                salt = bytes.fromhex(salt_hex)
                enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)
                for _rel, _path, _sz, s, page1 in db_files:
                    if s == salt_hex and decrypt_core.verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = enc_key.hex()
                        break
                if (i + 1) % 3 == 0:
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'密钥派生: {i+1}/{len(remaining_salts)}', 'progress': (i+1)/len(remaining_salts)*30})}\n\n"

        if not key_map:
            yield f"data: {json.dumps({'type': 'error', 'message': '无法验证任何密钥，passphrase 可能已失效，请重新捕获'})}\n\n"
            return

        # Save updated keys
        keys_data = {"_db_dir": db_dir}
        for rel, path, sz, salt, page1 in db_files:
            if salt in key_map:
                keys_data[rel] = {"enc_key": key_map[salt], "salt": salt}
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys_data, f, indent=2)

        # Decrypt all databases
        yield f"data: {json.dumps({'type': 'info', 'message': '解密数据库中...'})}\n\n"
        out_dir = DB_BASE
        os.makedirs(out_dir, exist_ok=True)

        success = 0
        failed = 0
        decrypt_files = [(rel, path, sz) for rel, path, sz, salt, page1 in db_files]
        decrypt_files.sort(key=lambda x: x[2])

        for idx, (rel, path, sz) in enumerate(decrypt_files):
            with open(path, "rb") as f:
                page1 = f.read(decrypt_core.PAGE_SZ)
            salt_hex = page1[:decrypt_core.SALT_SZ].hex()
            if salt_hex not in key_map:
                continue

            enc_key = bytes.fromhex(key_map[salt_hex])
            out_path = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            try:
                if decrypt_core._decrypt_database(path, out_path, enc_key):
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            progress = 30 + (idx + 1) / len(decrypt_files) * 70
            if (idx + 1) % 3 == 0 or idx == len(decrypt_files) - 1:
                yield f"data: {json.dumps({'type': 'progress', 'message': f'解密: {idx+1}/{len(decrypt_files)}', 'progress': progress})}\n\n"

        # Clear caches so new data is picked up
        global _table_index_cache, _stats_cache, MESSAGE_DBS, BIZ_MESSAGE_DBS
        _table_index_cache = None
        _stats_cache = None
        MESSAGE_DBS, BIZ_MESSAGE_DBS = _discover_message_dbs()

        # Rebuild contacts cache after sync
        _rebuild_contacts_cache()

        # Push real-time event to all connected clients
        _push_event({"type": "sync_complete", "new_messages": success})
        _push_event({"type": "contacts_updated"})

        yield f"data: {json.dumps({'type': 'done', 'message': f'✅ 同步完成！成功 {success} 个数据库', 'success': success, 'failed': failed})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/contacts/cached")
def api_contacts_cached():
    """Return contacts from JSON cache for fast page loads."""
    try:
        if os.path.exists(CONTACTS_CACHE_FILE):
            with open(CONTACTS_CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            return jsonify(cache_data)
    except (json.JSONDecodeError, IOError, KeyError) as e:
        print(f"[Cache] Cache file corrupted or unreadable, falling through to DB: {e}")

    # Cache miss or error — fall through to full query and build cache
    enriched = _rebuild_contacts_cache()
    if enriched is not None:
        return jsonify({"updated_at": int(time.time()), "contacts": enriched})
    # If rebuild also failed, return empty
    return jsonify({"updated_at": 0, "contacts": []})


@app.route("/api/contacts")
def api_contacts():
    """Return contacts with message counts, sorted by activity."""
    # Support ?refresh=1 to force cache invalidation
    if request.args.get("refresh"):
        global _table_index_cache, _stats_cache, MESSAGE_DBS, BIZ_MESSAGE_DBS
        _table_index_cache = None
        _stats_cache = None
        MESSAGE_DBS, BIZ_MESSAGE_DBS = _discover_message_dbs()

    contacts = get_all_contacts()
    # Build message count and last active info
    enriched = []
    for c in contacts:
        count = get_message_count(c["username"])
        if count == 0:
            continue  # Skip contacts with no messages
        last_time = get_last_message_time(c["username"])
        c["message_count"] = count
        c["last_active"] = (
            datetime.fromtimestamp(last_time).strftime("%Y-%m-%d")
            if last_time else ""
        )
        c["last_active_ts"] = last_time
        display_name = c["remark"] or c["nick_name"] or c["username"]
        c["display_name"] = display_name
        enriched.append(c)
    # Sort by last active time (most recent first)
    enriched.sort(key=lambda x: x["last_active_ts"], reverse=True)

    # Update the JSON cache file
    try:
        cache_data = {
            "updated_at": int(time.time()),
            "contacts": enriched
        }
        with open(CONTACTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[Cache] Failed to write contacts cache: {e}")

    return jsonify(enriched)


@app.route("/api/messages/<path:username>")
def api_messages(username):
    """Return last 50 messages for a contact."""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    messages = get_messages(username, limit=limit, offset=offset)
    return jsonify(messages)


@app.route("/api/messages/<path:username>/stats")
def api_messages_stats(username):
    """Return message stats for a contact: total count, date range."""
    count = get_message_count(username)
    last_time = get_last_message_time(username)
    # Get first message time
    tables = find_message_table(username)
    first_time = 0
    for db_path, table_name in tables:
        try:
            conn = get_db(db_path)
            cursor = conn.execute(f"SELECT MIN(create_time) FROM [{table_name}]")
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                if first_time == 0 or row[0] < first_time:
                    first_time = row[0]
        except Exception:
            continue
    return jsonify({
        "count": count,
        "first_time": first_time,
        "last_time": last_time,
        "first_date": datetime.fromtimestamp(first_time).strftime("%Y-%m-%d") if first_time else "",
        "last_date": datetime.fromtimestamp(last_time).strftime("%Y-%m-%d") if last_time else "",
    })


@app.route("/api/export", methods=["POST"])
def api_export():
    """Export selected conversations in the chosen format as a zip."""
    data = request.get_json()
    usernames = data.get("usernames", [])
    fmt = data.get("format", "txt")

    if not usernames:
        return jsonify({"error": "No contacts selected"}), 400

    # Get contact info for display names
    contacts = get_all_contacts()
    contact_map = {c["username"]: c for c in contacts}

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for username in usernames:
            contact = contact_map.get(username, {})
            display_name = (
                contact.get("remark") or
                contact.get("nick_name") or
                username
            )
            # Sanitize filename
            safe_name = "".join(
                c for c in display_name if c.isalnum() or c in " _-"
            ).strip()
            if not safe_name:
                safe_name = username.replace("@", "_at_")

            messages = get_all_messages(username)

            if fmt == "txt":
                content = export_txt(display_name, messages)
                zf.writestr(f"{safe_name}.txt", content)
            elif fmt == "csv":
                content = export_csv(display_name, messages)
                zf.writestr(f"{safe_name}.csv", content)
            elif fmt == "html":
                content = export_html(display_name, messages)
                zf.writestr(f"{safe_name}.html", content)

    zip_buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"wechat_export_{timestamp}.zip"
    )


WECHAT_ATTACH_BASE = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)

def _find_wechat_user_dir():
    """Find the WeChat user data directory."""
    if not os.path.exists(WECHAT_ATTACH_BASE):
        return None
    for item in os.listdir(WECHAT_ATTACH_BASE):
        full = os.path.join(WECHAT_ATTACH_BASE, item)
        if os.path.isdir(full) and item not in ("all_users", "Backup"):
            attach_dir = os.path.join(full, "msg", "attach")
            if os.path.exists(attach_dir):
                return full
    return None


@app.route("/api/image/<username>/<path:filename>")
def api_image(username, filename):
    """Serve an image file from WeChat's attach directory."""
    # Path traversal prevention
    if '..' in filename or '/' in filename or '\\' in filename:
        return "Invalid filename", 400
    user_dir = _find_wechat_user_dir()
    if not user_dir:
        return "Not found", 404

    conv_hash = md5_hash(username)
    attach_dir = os.path.join(user_dir, "msg", "attach", conv_hash)

    # Search for the file in all date subfolders
    for root, dirs, files in os.walk(attach_dir):
        if filename in files:
            file_path = os.path.join(root, filename)
            # Check if it's a readable image format
            with open(file_path, "rb") as f:
                header = f.read(4)
            if header[:2] == b'\xff\xd8':
                return send_file(file_path, mimetype="image/jpeg")
            elif header[:4] == b'\x89PNG':
                return send_file(file_path, mimetype="image/png")
            elif header[:4] == b'GIF8':
                return send_file(file_path, mimetype="image/gif")
            else:
                # V2 encrypted - can't serve
                return "Encrypted image (V2)", 415

    # If exact filename not found, try _M variant (medium quality, often JPEG)
    base = filename.rsplit('.', 1)[0]
    m_filename = base + "_M.dat"
    for root, dirs, files in os.walk(attach_dir):
        if m_filename in files:
            file_path = os.path.join(root, m_filename)
            with open(file_path, "rb") as f:
                header = f.read(2)
            if header == b'\xff\xd8':
                return send_file(file_path, mimetype="image/jpeg")

    return "Not found", 404


@app.route("/api/images/<username>")
def api_images_list(username):
    """List available image files for a conversation, grouped by month."""
    user_dir = _find_wechat_user_dir()
    if not user_dir:
        return jsonify([])

    conv_hash = md5_hash(username)
    attach_dir = os.path.join(user_dir, "msg", "attach", conv_hash)
    if not os.path.exists(attach_dir):
        return jsonify([])

    images = []
    for date_folder in sorted(os.listdir(attach_dir), reverse=True):
        img_dir = os.path.join(attach_dir, date_folder, "Img")
        if not os.path.isdir(img_dir):
            continue
        for fname in os.listdir(img_dir):
            if not fname.endswith(".dat"):
                continue
            # Skip thumbnails for listing, prefer _M (medium) or plain
            if fname.endswith("_t.dat"):
                continue
            fpath = os.path.join(img_dir, fname)
            # Quick check if it's a readable image
            try:
                with open(fpath, "rb") as f:
                    h = f.read(2)
                if h in (b'\xff\xd8', b'\x89\x50', b'\x47\x49'):
                    images.append({
                        "filename": fname,
                        "month": date_folder,
                        "size": os.path.getsize(fpath),
                        "url": f"/api/image/{username}/{fname}",
                    })
            except (IOError, OSError):
                continue

    return jsonify(images[:200])  # Limit to 200 most recent


# Cache: {conv_hash: {month: [sorted list of (mtime, filename)]}}
_image_index_cache = {}


def _build_image_index(username):
    """Build a time-sorted index of images for a conversation (cached)."""
    conv_hash = md5_hash(username)
    if conv_hash in _image_index_cache:
        return _image_index_cache[conv_hash]

    user_dir = _find_wechat_user_dir()
    if not user_dir:
        return {}

    attach_dir = os.path.join(user_dir, "msg", "attach", conv_hash)
    if not os.path.exists(attach_dir):
        return {}

    index = {}  # {month: [(mtime, filename), ...]}
    for date_folder in os.listdir(attach_dir):
        img_dir = os.path.join(attach_dir, date_folder, "Img")
        if not os.path.isdir(img_dir):
            continue
        files = []
        for fname in os.listdir(img_dir):
            if fname.endswith("_t.dat") or not fname.endswith(".dat"):
                continue
            fpath = os.path.join(img_dir, fname)
            try:
                mtime = int(os.path.getmtime(fpath))
                files.append((mtime, fname))
            except OSError:
                continue
        files.sort()
        index[date_folder] = files

    _image_index_cache[conv_hash] = index
    return index


def _find_image_for_message(username, create_time):
    """Find image file for a message using cached index. Fast O(log n) lookup."""
    index = _build_image_index(username)
    if not index:
        return None

    msg_date = datetime.fromtimestamp(create_time)
    month_folder = msg_date.strftime("%Y-%m")
    files = index.get(month_folder, [])
    if not files:
        return None

    # Binary search for closest mtime
    import bisect
    pos = bisect.bisect_left(files, (create_time,))

    # Check neighbors
    best = None
    best_diff = 120  # max 2 minutes tolerance
    for i in range(max(0, pos - 2), min(len(files), pos + 3)):
        diff = abs(files[i][0] - create_time)
        if diff < best_diff:
            best_diff = diff
            best = files[i][1]

    # Verify it's a readable image (quick header check)
    if best:
        user_dir = _find_wechat_user_dir()
        conv_hash = md5_hash(username)
        fpath = os.path.join(user_dir, "msg", "attach", conv_hash, month_folder, "Img", best)
        try:
            with open(fpath, "rb") as f:
                h = f.read(2)
            if h not in (b'\xff\xd8', b'\x89\x50', b'\x47\x49'):
                return None
        except (IOError, OSError):
            return None

    return best


def export_txt(display_name, messages):
    """Export messages as plain text."""
    lines = [f"Chat History: {display_name}", "=" * 60, ""]
    for msg in messages:
        sender = "Me" if msg["is_self"] else "Other"
        lines.append(f"[{msg['time_str']}] {sender}: {msg['content']}")
    return "\n".join(lines)


def export_csv(display_name, messages):
    """Export messages as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Time", "Sender", "Type", "Content"])
    for msg in messages:
        sender = "Me" if msg["is_self"] else "Other"
        writer.writerow([msg["time_str"], sender, msg["type"], msg["content"]])
    return output.getvalue()


def export_html(display_name, messages):
    """Export messages as styled HTML with chat bubbles."""
    html_parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Chat: {display_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #ebebeb; padding: 20px; }}
.header {{ text-align: center; padding: 16px; background: #ededed;
           border-radius: 8px; margin-bottom: 20px; }}
.header h1 {{ font-size: 18px; color: #333; }}
.chat-container {{ max-width: 600px; margin: 0 auto; }}
.message {{ display: flex; margin-bottom: 12px; align-items: flex-start; }}
.message.sent {{ justify-content: flex-end; }}
.message.received {{ justify-content: flex-start; }}
.bubble {{ max-width: 70%; padding: 10px 14px; border-radius: 8px;
           font-size: 14px; line-height: 1.5; word-wrap: break-word; }}
.sent .bubble {{ background: #95ec69; color: #000; border-top-right-radius: 2px; }}
.received .bubble {{ background: #fff; color: #000; border-top-left-radius: 2px; }}
.time {{ font-size: 11px; color: #999; text-align: center;
         margin: 16px 0 8px; }}
.meta {{ font-size: 10px; color: #aaa; margin-top: 4px; }}
</style>
</head>
<body>
<div class="header"><h1>{display_name}</h1></div>
<div class="chat-container">
"""]
    last_date = ""
    for msg in messages:
        msg_date = msg["time_str"][:10] if msg["time_str"] else ""
        if msg_date != last_date:
            html_parts.append(
                f'<div class="time">{msg["time_str"][:16]}</div>'
            )
            last_date = msg_date

        direction = "sent" if msg["is_self"] else "received"
        import html
        safe_content = html.escape(msg["content"]).replace("\n", "<br>")
        html_parts.append(f"""<div class="message {direction}">
<div class="bubble">{safe_content}</div>
</div>""")

    html_parts.append("</div></body></html>")
    return "\n".join(html_parts)


# ===== AI Chat Feature =====

AI_CONFIG_FILE = os.path.join(PROJECT_DIR, "ai_config.json")
AI_SESSIONS_FILE = os.path.join(PROJECT_DIR, "ai_sessions.json")

# In-memory session store: {session_id: {messages: [...], contacts: [...], title: str, updated: timestamp}}
_ai_sessions = {}


def _load_sessions():
    """Load sessions from disk on startup."""
    global _ai_sessions
    if os.path.exists(AI_SESSIONS_FILE):
        try:
            with open(AI_SESSIONS_FILE, "r", encoding="utf-8") as f:
                _ai_sessions = json.load(f)
        except Exception:
            _ai_sessions = {}


def _save_sessions():
    """Persist sessions to disk."""
    try:
        with open(AI_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_ai_sessions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_load_sessions()


def load_ai_config():
    """Load AI configuration from file."""
    if os.path.exists(AI_CONFIG_FILE):
        with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"api_base": "https://api.moonshot.cn/v1", "api_key": "", "model": "kimi-k2-0711-128k"}


def save_ai_config(config):
    """Save AI configuration to file."""
    with open(AI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


@app.route("/ai")
def ai_page():
    return render_template("ai.html")


@app.route("/api/ai/config", methods=["GET", "POST"])
def api_ai_config():
    if request.method == "GET":
        config = load_ai_config()
        # Mask the API key for security
        masked = dict(config)
        if masked.get("api_key"):
            key = masked["api_key"]
            if len(key) > 8:
                masked["api_key"] = key[:4] + "*" * (len(key) - 8) + key[-4:]
            else:
                masked["api_key"] = "****"
        return jsonify(masked)
    else:
        data = request.get_json()
        config = load_ai_config()
        if data.get("api_base"):
            new_base = data["api_base"].rstrip("/")
            # SSRF prevention: only allow known AI API domains
            from urllib.parse import urlparse
            parsed = urlparse(new_base)
            ALLOWED_HOSTS = [
                "api.moonshot.cn", "api.openai.com", "api.anthropic.com",
                "api.deepseek.com", "api.together.xyz", "api.groq.com",
                "generativelanguage.googleapis.com", "dashscope.aliyuncs.com",
                "api.siliconflow.cn", "api.lingyiwanwu.com", "api.baichuan-ai.com",
                "api.minimax.chat", "api.zhipuai.cn",
            ]
            if parsed.scheme != "https":
                return jsonify({"error": "Only HTTPS API URLs are allowed"}), 400
            if not any(parsed.hostname == h or (parsed.hostname and parsed.hostname.endswith("." + h)) for h in ALLOWED_HOSTS):
                return jsonify({"error": f"API host '{parsed.hostname}' not in allowed list. Contact admin to add it."}), 400
            config["api_base"] = new_base
        if data.get("api_key") and "****" not in data["api_key"]:
            config["api_key"] = data["api_key"]
        if data.get("model"):
            config["model"] = data["model"]
        save_ai_config(config)
        return jsonify({"success": True})


@app.route("/api/ai/sessions", methods=["GET"])
def api_ai_sessions():
    """List all sessions, sorted by last updated."""
    sessions = []
    for sid, data in _ai_sessions.items():
        sessions.append({
            "id": sid,
            "title": data.get("title", "New Chat"),
            "contacts": data.get("contacts", []),
            "message_count": len(data.get("messages", [])),
            "updated": data.get("updated", 0),
        })
    sessions.sort(key=lambda s: s["updated"], reverse=True)
    return jsonify(sessions)


@app.route("/api/ai/sessions", methods=["POST"])
def api_ai_session_create():
    """Create a new session. Returns session_id."""
    import uuid
    sid = str(uuid.uuid4())[:8]
    data = request.get_json() or {}
    _ai_sessions[sid] = {
        "messages": [],
        "contacts": data.get("contacts", []),
        "title": data.get("title", "New Chat"),
        "updated": time.time(),
    }
    _save_sessions()
    return jsonify({"id": sid})


@app.route("/api/ai/sessions/<sid>", methods=["GET"])
def api_ai_session_get(sid):
    """Get a session's full state."""
    session = _ai_sessions.get(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"id": sid, **session})


@app.route("/api/ai/sessions/<sid>", methods=["PUT"])
def api_ai_session_update(sid):
    """Update session (save messages, contacts, title)."""
    session = _ai_sessions.get(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    if "messages" in data:
        session["messages"] = data["messages"]
    if "contacts" in data:
        session["contacts"] = data["contacts"]
    if "title" in data:
        session["title"] = data["title"]
    session["updated"] = time.time()
    _save_sessions()
    return jsonify({"ok": True})


@app.route("/api/ai/sessions/<sid>", methods=["DELETE"])
def api_ai_session_delete(sid):
    """Delete a session."""
    if sid in _ai_sessions:
        del _ai_sessions[sid]
        _save_sessions()
    return jsonify({"ok": True})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """Stream AI chat response via SSE."""
    import requests as req_lib

    data = request.get_json()
    user_messages = data.get("messages", [])
    context_username = data.get("context_username")
    context_usernames = data.get("context_usernames", [])
    thinking_param = data.get("thinking")
    file_ids = data.get("file_ids", [])
    images = data.get("images", [])

    # Support both single and multiple context usernames
    if context_username and not context_usernames:
        context_usernames = [context_username]

    print(f"\n{'='*60}")
    print(f"[AI Chat] New request")
    print(f"  Context usernames: {context_usernames}")
    print(f"  User messages count: {len(user_messages)}")
    print(f"  Thinking param: {thinking_param}")
    print(f"  File IDs: {file_ids}")
    print(f"  Images count: {len(images)}")

    config = load_ai_config()
    print(f"  Model: {config.get('model')}")
    print(f"  API Base: {config.get('api_base')}")
    print(f"  API Key: {config.get('api_key', '')[:8]}...")

    if not config.get("api_key"):
        print(f"  [ERROR] API Key not configured")
        def error_gen():
            yield f"data: {json.dumps({'error': 'API Key not configured'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
        return Response(stream_with_context(error_gen()), mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Build messages list
    api_messages = []

    # System prompt: instruct model to think and respond in Chinese
    api_messages.append({
        "role": "system",
        "content": "请始终使用中文进行思考和回答。推理过程也必须使用中文。"
    })

    # Add file references as system messages
    if file_ids:
        file_content_parts = []
        for fid in file_ids:
            file_content_parts.append(f"fileid://{fid}")
        file_system_msg = {"role": "system", "content": "\n".join(file_content_parts)}
        api_messages.append(file_system_msg)
        print(f"  File references added: {len(file_ids)} files")

    # Load chat messages as context for selected contacts
    if context_usernames:
        all_contacts = get_all_contacts()
        contact_map = {c["username"]: c for c in all_contacts}
        context_lines = []

        # Time range filter (optional)
        time_from = data.get("time_from", 0)  # unix timestamp
        time_to = data.get("time_to", 0)  # unix timestamp
        context_limit = data.get("context_limit", 200)  # max messages per contact


        for uname in context_usernames:
            # Load messages with time range if specified
            if time_from or time_to:
                chat_messages = get_messages_by_timerange(uname, time_from, time_to, limit=context_limit)
            else:
                chat_messages = get_messages(uname, limit=context_limit, offset=0, skip_images=True)
            if not chat_messages:
                print(f"  [WARN] No messages found for: {uname}")
                continue
            contact_info = contact_map.get(uname, {})
            display_name = contact_info.get("remark") or contact_info.get("nick_name") or uname
            print(f"  Context loaded: {display_name} ({len(chat_messages)} messages)")

            context_lines.append(f"\n--- 与「{display_name}」的最近聊天记录 ---\n")
            for msg in chat_messages:
                sender = "我" if msg["is_self"] else (msg.get("sender_name") or display_name)
                context_lines.append(f"[{msg['time_str']}] {sender}: {msg['content']}")

        if context_lines:
            system_content = "以下是用户选择的聊天记录（供分析参考）：\n" + "\n".join(context_lines)
            api_messages.append({"role": "system", "content": system_content})
            print(f"  System context: {len(system_content)} chars")

    # Add user conversation messages
    api_messages.extend(user_messages)

    # Build multimodal content when images are present
    if images and api_messages:
        # Find the last user message and convert its content to multimodal format
        for i in range(len(api_messages) - 1, -1, -1):
            if api_messages[i].get("role") == "user":
                original_content = api_messages[i].get("content", "")
                content_parts = []
                # Add text part
                if original_content:
                    content_parts.append({"type": "text", "text": original_content})
                # Add image parts
                for img in images:
                    img_data = img.get("data", "") if isinstance(img, dict) else img
                    img_name = img.get("name", "image") if isinstance(img, dict) else "image"
                    # Ensure proper data URI format for base64 images
                    if img_data and not img_data.startswith("data:"):
                        # Detect image type from name or default to jpeg
                        ext = img_name.rsplit(".", 1)[-1].lower() if "." in img_name else "jpeg"
                        mime_map = {"png": "image/png", "gif": "image/gif", "webp": "image/webp",
                                    "jpg": "image/jpeg", "jpeg": "image/jpeg"}
                        mime = mime_map.get(ext, "image/jpeg")
                        img_data = f"data:{mime};base64,{img_data}"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img_data}
                    })
                    print(f"  Image added: {img_name} ({len(img_data)} chars)")
                api_messages[i]["content"] = content_parts
                break

    print(f"  Total API messages: {len(api_messages)}")
    if user_messages:
        last_msg = user_messages[-1].get("content", "")
        if isinstance(last_msg, str):
            print(f"  Last user message: {last_msg[:100]}")
    print(f"{'='*60}")

    def generate():
        max_retries = 3
        retry_delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                api_url = f"{config['api_base']}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": config["model"],
                    "messages": api_messages,
                    "stream": True,
                }

                # Handle thinking parameter
                if thinking_param is False:
                    payload["thinking"] = {"type": "disabled"}
                elif thinking_param is True:
                    payload["thinking"] = {"type": "enabled", "budget_tokens": 4096}
                # If "auto" or not set, don't add thinking param

                print(f"[AI Chat] Calling API: {api_url} (attempt {attempt+1}/{max_retries})")
                print(f"[AI Chat] Payload: model={config['model']}, messages={len(api_messages)}, stream=True, thinking={thinking_param}")

                resp = req_lib.post(api_url, json=payload, headers=headers, stream=True, timeout=120)
                print(f"[AI Chat] Response status: {resp.status_code}")
                print(f"[AI Chat] Response encoding: {resp.encoding}")

                if resp.status_code != 200:
                    error_msg = f"API returned status {resp.status_code}"
                    try:
                        error_body = resp.json()
                        if "error" in error_body:
                            error_msg = error_body["error"].get("message", error_msg)
                    except Exception:
                        pass

                    # Retry on overload/rate limit (429 or 503 or "overloaded" in message)
                    is_retryable = (
                        resp.status_code in (429, 503) or
                        "overload" in error_msg.lower() or
                        "rate" in error_msg.lower() or
                        "try again" in error_msg.lower()
                    )
                    if is_retryable and attempt < max_retries - 1:
                        wait = retry_delay * (attempt + 1)
                        retry_msg = f"\n\n⏳ 服务繁忙，{wait}秒后自动重试 ({attempt+1}/{max_retries})...\n\n"
                        yield f"data: {json.dumps({'content': retry_msg}, ensure_ascii=False)}\n\n"
                        time.sleep(wait)
                        continue

                    yield f"data: {json.dumps({'error': error_msg}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                    return

                # Success - stream response
                thinking_buffer = ""
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace")
                    if line.startswith("data: "):
                        payload_str = line[6:]
                        if payload_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload_str)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                # Handle both content and reasoning_content (thinking models like Kimi K3)
                                content = delta.get("content")
                                reasoning = delta.get("reasoning_content")
                                if content:
                                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                                elif reasoning:
                                    # Buffer thinking output and yield when >= 20 chars
                                    thinking_buffer += reasoning
                                    if len(thinking_buffer) >= 20:
                                        yield f"data: {json.dumps({'thinking': thinking_buffer}, ensure_ascii=False)}\n\n"
                                        thinking_buffer = ""
                        except json.JSONDecodeError:
                            continue

                # Flush remaining thinking buffer
                if thinking_buffer:
                    yield f"data: {json.dumps({'thinking': thinking_buffer}, ensure_ascii=False)}\n\n"

                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                return  # Success, exit retry loop

            except req_lib.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait = retry_delay * (attempt + 1)
                    timeout_msg = f"\n\n⏳ 请求超时，{wait}秒后重试 ({attempt+1}/{max_retries})...\n\n"
                    yield f"data: {json.dumps({'content': timeout_msg}, ensure_ascii=False)}\n\n"
                    time.sleep(wait)
                    continue
                yield f"data: {json.dumps({'error': 'Request timed out after retries'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
            except req_lib.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    wait = retry_delay * (attempt + 1)
                    conn_msg = f"\n\n⏳ 连接错误，{wait}秒后重试...\n\n"
                    yield f"data: {json.dumps({'content': conn_msg}, ensure_ascii=False)}\n\n"
                    time.sleep(wait)
                    continue
                err_str = str(e)
                yield f"data: {json.dumps({'error': 'Connection error: ' + err_str}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
            except Exception as e:
                err_str = str(e)
                yield f"data: {json.dumps({'error': 'Unexpected error: ' + err_str}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                return

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/ai/upload", methods=["POST"])
def api_ai_upload():
    """Upload a file to the AI API and return file_id."""
    config = load_ai_config()
    if not config.get("api_key"):
        return jsonify({"error": "API Key not configured"}), 400

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    import requests as req_lib
    try:
        resp = req_lib.post(
            f"{config['api_base']}/files",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            files={"file": (file.filename, file.stream, file.content_type)},
            data={"purpose": "file-extract"},
            timeout=60
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"[AI Upload] File uploaded: {file.filename} -> {result.get('id')}")
            return jsonify(result)
        else:
            return jsonify({"error": f"Upload failed: {resp.status_code}"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===== Article Scraper Feature =====

SCRAPE_DIR = os.path.join(PROJECT_DIR, "scraped_articles")
_scrape_jobs = {}  # {job_id: {status, ...}}

# Scraper session persistence (similar to AI sessions)
SCRAPER_SESSIONS_FILE = os.path.join(PROJECT_DIR, "scraper_sessions.json")
_scraper_sessions = {}


def _load_scraper_sessions():
    """Load scraper sessions from disk."""
    global _scraper_sessions
    if os.path.exists(SCRAPER_SESSIONS_FILE):
        try:
            with open(SCRAPER_SESSIONS_FILE, "r", encoding="utf-8") as f:
                _scraper_sessions = json.load(f)
        except Exception:
            _scraper_sessions = {}


def _save_scraper_sessions():
    """Save scraper sessions to disk."""
    try:
        with open(SCRAPER_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_scraper_sessions, f, ensure_ascii=False, indent=2)
        os.chmod(SCRAPER_SESSIONS_FILE, 0o600)
    except Exception:
        pass


_load_scraper_sessions()


def extract_article_urls_for_contact(username):
    """Extract all article URLs from an official account's messages.

    Returns: [{title, url, timestamp, time_str}]
    """
    tables = find_message_table(username)
    if not tables:
        return []

    import re
    urls_seen = set()
    articles = []

    # Regex patterns (same as used in get_messages())
    mmreader_re = re.compile(
        r'<(?:item|newitem)>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>'
        r'.*?<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>.*?</(?:item|newitem)>',
        re.DOTALL
    )
    title_re = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>")
    url_re = re.compile(r"<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>")

    for db_path, table_name in tables:
        try:
            conn = get_db(db_path)
            cursor = conn.execute(
                f"SELECT local_type, create_time, message_content, "
                f"WCDB_CT_message_content FROM [{table_name}] "
                f"ORDER BY create_time ASC"
            )
            for row in cursor:
                local_type = row["local_type"]
                base_type = get_base_msg_type(local_type)
                ct_type = row["WCDB_CT_message_content"] or 0
                content = decode_message_content(row["message_content"], ct_type)
                create_time = row["create_time"] or 0

                if not content:
                    continue

                found_urls = []

                # Type 49 (link/appmsg) or type 1 with mmreader content
                if base_type == 49 or (base_type == 1 and "<mmreader>" in str(content)):
                    # Try mmreader (multi-article) first
                    if "<mmreader>" in str(content):
                        for title, url in mmreader_re.findall(str(content)):
                            if url and url.startswith("http"):
                                found_urls.append((title.strip(), url.strip()))
                    else:
                        # Standard single appmsg
                        title_match = title_re.search(str(content))
                        url_match = url_re.search(str(content))
                        if title_match and url_match:
                            title = title_match.group(1).strip()
                            url = url_match.group(1).strip()
                            if url.startswith("http"):
                                found_urls.append((title, url))

                for title, url in found_urls:
                    # Deduplicate by full URL (WeChat articles differ only in query params)
                    if url in urls_seen:
                        continue
                    urls_seen.add(url)
                    articles.append({
                        "title": title,
                        "url": url,
                        "timestamp": create_time,
                        "time_str": datetime.fromtimestamp(create_time).strftime(
                            "%Y-%m-%d %H:%M"
                        ) if create_time else "",
                    })

            conn.close()
        except Exception:
            continue

    # Sort by timestamp (newest first)
    articles.sort(key=lambda a: a["timestamp"], reverse=True)
    return articles


@app.route("/scraper")
def scraper_page():
    """Render the article scraper page."""
    status = check_setup_status()
    if status["step"] != 99:
        return render_template("setup.html", status=status)
    return render_template("scraper.html")


@app.route("/api/articles/urls/<path:username>")
def api_article_urls(username):
    """Extract all article URLs from an official account's messages."""
    articles = extract_article_urls_for_contact(username)
    return jsonify(articles)


@app.route("/api/articles/urls/batch", methods=["POST"])
def api_article_urls_batch():
    """Extract URLs from multiple official accounts at once."""
    data = request.get_json()
    usernames = data.get("usernames", [])
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400

    result = {}
    for username in usernames:
        result[username] = extract_article_urls_for_contact(username)
    return jsonify(result)


@app.route("/api/articles/scrape/start", methods=["POST"])
def api_articles_scrape_start():
    """Submit a scrape job. Returns job_id."""
    import uuid

    data = request.get_json()
    articles = data.get("articles", [])
    prompt = data.get("prompt", "")

    if not articles:
        return jsonify({"error": "No articles provided"}), 400

    job_id = str(uuid.uuid4())[:8]
    os.makedirs(SCRAPE_DIR, exist_ok=True)

    # Save job to disk
    job_data = {
        "articles": articles,
        "prompt": prompt,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    }
    job_file = os.path.join(SCRAPE_DIR, f"{job_id}.json")
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job_data, f, ensure_ascii=False, indent=2)

    _scrape_jobs[job_id] = {"status": "pending"}
    return jsonify({"job_id": job_id, "article_count": len(articles)})


@app.route("/api/articles/scrape")
def api_articles_scrape():
    """SSE endpoint: run scraper subprocess and stream progress."""
    job_id = request.args.get("job_id")
    if not job_id:
        def err():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Missing job_id'})}\n\n"
        return Response(stream_with_context(err()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    job_file = os.path.join(SCRAPE_DIR, f"{job_id}.json")
    if not os.path.exists(job_file):
        def err():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
        return Response(stream_with_context(err()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def generate():
        # Find the Python interpreter (same one running this app)
        python_bin = sys.executable
        scraper_script = os.path.join(PROJECT_DIR, "scraper.py")

        yield f"data: {json.dumps({'type': 'info', 'message': '启动爬虫...'})}\n\n"

        try:
            proc = subprocess.Popen(
                [python_bin, scraper_script, "scrape", "--job", job_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=PROJECT_DIR,
            )

            # Read stdout line by line and forward as SSE
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError:
                    # Non-JSON output, wrap it
                    yield f"data: {json.dumps({'type': 'info', 'message': line})}\n\n"

            proc.wait()

            if proc.returncode != 0:
                stderr = proc.stderr.read()
                yield f"data: {json.dumps({'type': 'error', 'message': f'爬虫进程异常退出: {stderr[:200]}'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'启动爬虫失败: {str(e)}'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/articles/analyze", methods=["POST"])
def api_articles_analyze():
    """Stream AI analysis of scraped articles via SSE."""
    import requests as req_lib

    data = request.get_json()
    job_id = data.get("job_id")
    prompt = data.get("prompt", "")

    # Default prompt
    if not prompt:
        prompt = (
            "给你这些公众号文章，按照时间排序，使用最先进的可视化分析方式帮我分析这些文章，"
            "然后以 html 可视化的方式输出。要求输出完整的独立 HTML 文件，包含 CSS 样式和必要的 "
            "JavaScript（可以使用 ECharts 或 Chart.js），确保可以直接在浏览器中打开查看。\n\n"
            "【重要】HTML 输出需要同时兼容屏幕浏览和 PDF 导出，请遵守以下规范：\n"
            "1. 使用深色背景（#1a1a2e 或类似）作为默认主题，但必须包含 @media print 样式块，"
            "在打印/导出 PDF 时切换为白底黑字\n"
            "2. 每个图表/卡片/section 添加 CSS: break-inside: avoid; page-break-inside: avoid; "
            "防止分页时被切割\n"
            "3. ECharts 图表使用 SVG 渲染器（renderer: 'svg'）而非默认 Canvas，"
            "这样导出 PDF 时图表是矢量的、清晰可缩放\n"
            "4. 图表容器设置明确的高度（如 300px-400px），不要用百分比高度\n"
            "5. 所有文字使用 system-ui 字体栈，确保中文渲染正确\n"
            "6. 页面整体宽度控制在 800px 以内居中显示，适配 A4 纸张宽度"
        )

    # Load scraped articles
    if job_id:
        job_file = os.path.join(SCRAPE_DIR, f"{job_id}.json")
        if not os.path.exists(job_file):
            def err():
                yield f"data: {json.dumps({'error': 'Job not found'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            return Response(stream_with_context(err()),
                            mimetype="text/event-stream; charset=utf-8",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        with open(job_file, "r", encoding="utf-8") as f:
            job = json.load(f)

        results = job.get("results", [])
        successful = [r for r in results if r.get("success")]
    else:
        # Direct articles content passed in body
        successful = data.get("articles", [])

    if not successful:
        def err():
            yield f"data: {json.dumps({'error': '没有成功爬取的文章可供分析'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        return Response(stream_with_context(err()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    config = load_ai_config()
    if not config.get("api_key"):
        def err():
            yield f"data: {json.dumps({'error': 'API Key not configured'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        return Response(stream_with_context(err()),
                        mimetype="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Build AI messages
    # Sort articles by timestamp
    successful.sort(key=lambda a: a.get("timestamp", 0))

    article_text_parts = []
    for i, article in enumerate(successful, 1):
        time_str = article.get("time_str", "")
        title = article.get("title", "Untitled")
        content = article.get("content", "")
        # Truncate very long articles to avoid context overflow
        if len(content) > 3000:
            content = content[:3000] + "\n... (内容已截断)"
        article_text_parts.append(
            f"--- 文章 {i}: {title} ({time_str}) ---\n{content}"
        )

    articles_context = "\n\n".join(article_text_parts)

    # System instructions for HTML generation quality
    html_system_guidance = (
        "当输出 HTML 可视化时，请遵守以下技术规范以确保导出 PDF 的质量：\n"
        "1. ECharts 使用 SVG 渲染器: init(dom, null, {renderer: 'svg'})\n"
        "2. 每个图表/卡片添加 break-inside: avoid 防止分页切割\n"
        "3. 包含 @media print 样式块（白底黑字、隐藏交互按钮）\n"
        "4. 图表容器使用固定高度（300-400px），不用百分比\n"
        "5. 页面内容宽度控制在 800px 以内，适配 A4"
    )

    # Support follow-up conversation: if messages array provided, use it
    conversation_messages = data.get("messages", [])
    if conversation_messages:
        # Multi-turn: system context + full conversation history
        api_messages = [
            {"role": "system", "content": "请始终使用中文进行思考和回答。推理过程也必须使用中文。"},
            {"role": "system", "content": f"以下是用户选择的公众号文章内容（共 {len(successful)} 篇）：\n\n{articles_context}"},
            {"role": "system", "content": html_system_guidance},
        ]
        for msg in conversation_messages:
            role = msg.get("role", "user")
            if role == "assistant":
                api_messages.append({"role": "assistant", "content": msg.get("content", "")})
            else:
                api_messages.append({"role": "user", "content": msg.get("content", "")})
    else:
        # Single-turn: first analysis
        api_messages = [
            {"role": "system", "content": "请始终使用中文进行思考和回答。推理过程也必须使用中文。"},
            {"role": "system", "content": f"以下是用户选择的公众号文章内容（共 {len(successful)} 篇）：\n\n{articles_context}"},
            {"role": "system", "content": html_system_guidance},
            {"role": "user", "content": prompt},
        ]

    print(f"\n{'='*60}")
    print(f"[Article Analysis] {len(successful)} articles, prompt: {prompt[:80]}...")
    print(f"  Context size: {len(articles_context)} chars")
    print(f"{'='*60}")

    def generate():
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                api_url = f"{config['api_base']}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": config["model"],
                    "messages": api_messages,
                    "stream": True,
                }

                resp = req_lib.post(api_url, json=payload, headers=headers,
                                    stream=True, timeout=180)

                if resp.status_code != 200:
                    error_msg = f"API returned status {resp.status_code}"
                    try:
                        error_body = resp.json()
                        if "error" in error_body:
                            error_msg = error_body["error"].get("message", error_msg)
                    except Exception:
                        pass

                    is_retryable = (
                        resp.status_code in (429, 503) or
                        "overload" in error_msg.lower() or
                        "rate" in error_msg.lower()
                    )
                    if is_retryable and attempt < max_retries - 1:
                        wait = retry_delay * (attempt + 1)
                        yield f"data: {json.dumps({'content': f'⏳ 服务繁忙，{wait}秒后重试...'}, ensure_ascii=False)}\n\n"
                        time.sleep(wait)
                        continue

                    yield f"data: {json.dumps({'error': error_msg}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return

                # Stream response
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace")
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
                                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                                elif reasoning:
                                    yield f"data: {json.dumps({'thinking': reasoning}, ensure_ascii=False)}\n\n"
                        except json.JSONDecodeError:
                            continue

                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            except req_lib.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait = retry_delay * (attempt + 1)
                    yield f"data: {json.dumps({'content': f'⏳ 超时，{wait}秒后重试...'}, ensure_ascii=False)}\n\n"
                    time.sleep(wait)
                    continue
                yield f"data: {json.dumps({'error': 'Request timed out'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ============================================================
# Scraper Session API
# ============================================================

@app.route("/api/scraper/sessions", methods=["GET"])
def api_scraper_sessions():
    """List all scraper sessions, sorted by last updated."""
    sessions = []
    for sid, data in _scraper_sessions.items():
        sessions.append({
            "id": sid,
            "title": data.get("title", "未命名"),
            "message_count": len(data.get("messages", [])),
            "updated": data.get("updated", 0),
        })
    sessions.sort(key=lambda s: s["updated"], reverse=True)
    return jsonify(sessions)


@app.route("/api/scraper/sessions", methods=["POST"])
def api_scraper_session_create():
    """Create a new scraper session."""
    import uuid
    sid = str(uuid.uuid4())[:8]
    data = request.get_json() or {}
    _scraper_sessions[sid] = {
        "messages": data.get("messages", []),
        "generated_html": data.get("generated_html", ""),
        "job_id": data.get("job_id"),
        "title": data.get("title", "未命名"),
        "scrape_summary": data.get("scrape_summary", False),
        "updated": time.time(),
    }
    _save_scraper_sessions()
    return jsonify({"id": sid})


@app.route("/api/scraper/sessions/<sid>", methods=["GET"])
def api_scraper_session_get(sid):
    """Get a scraper session's full state."""
    session = _scraper_sessions.get(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"id": sid, **session})


@app.route("/api/scraper/sessions/<sid>", methods=["PUT"])
def api_scraper_session_update(sid):
    """Update a scraper session."""
    session = _scraper_sessions.get(sid)
    if not session:
        # Create it
        session = {}
        _scraper_sessions[sid] = session
    data = request.get_json() or {}
    if "messages" in data:
        session["messages"] = data["messages"]
    if "generated_html" in data:
        session["generated_html"] = data["generated_html"]
    if "job_id" in data:
        session["job_id"] = data["job_id"]
    if "title" in data:
        session["title"] = data["title"]
    if "scrape_summary" in data:
        session["scrape_summary"] = data["scrape_summary"]
    session["updated"] = time.time()
    _save_scraper_sessions()
    return jsonify({"ok": True})


@app.route("/api/scraper/sessions/<sid>", methods=["DELETE"])
def api_scraper_session_delete(sid):
    """Delete a scraper session."""
    if sid in _scraper_sessions:
        del _scraper_sessions[sid]
        _save_scraper_sessions()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9527, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
