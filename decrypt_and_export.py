#!/usr/bin/env python3
"""
WeChat Mac Database Decryptor & Chat History Exporter

Decrypts WeChat Mac SQLCipher databases and exports chat history.

Usage:
    python3 decrypt_and_export.py --key <64-char-hex-key>
    python3 decrypt_and_export.py --key-file <path-to-key-file>

Requires: pycryptodome (pip3 install pycryptodome)
"""

import argparse
import hashlib
import hmac
import os
import shutil
import sqlite3
import sys
import json
from datetime import datetime
from pathlib import Path

# SQLCipher constants
KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096
DEFAULT_ITER = 64000
SQLITE_FILE_HEADER = b"SQLite format 3\x00"

# WeChat data path on macOS
WECHAT_BASE_PATH = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)


def decrypt_database(key_hex, db_path, out_path):
    """
    Decrypt a SQLCipher database using the provided key.
    Same algorithm as WeChat Windows (AES-256-CBC with HMAC-SHA1).
    """
    if not os.path.exists(db_path):
        return False, f"File not found: {db_path}"

    if len(key_hex) != 64:
        return False, f"Key must be 64 hex characters, got {len(key_hex)}"

    password = bytes.fromhex(key_hex.strip())

    with open(db_path, "rb") as f:
        blist = f.read()

    if len(blist) < DEFAULT_PAGESIZE:
        return False, f"File too small: {db_path}"

    salt = blist[:16]
    byte_key = hashlib.pbkdf2_hmac("sha1", password, salt, DEFAULT_ITER, KEY_SIZE)
    first = blist[16:DEFAULT_PAGESIZE]

    # Verify key
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
    hash_mac = hmac.new(mac_key, first[:-32], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')

    if hash_mac.digest() != first[-32:-12]:
        return False, f"Key verification failed for: {db_path}"

    # Decrypt
    from Crypto.Cipher import AES

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    pages = [blist[i:i + DEFAULT_PAGESIZE] for i in range(DEFAULT_PAGESIZE, len(blist), DEFAULT_PAGESIZE)]

    with open(out_path, "wb") as out_file:
        # First page
        out_file.write(SQLITE_FILE_HEADER)
        t = AES.new(byte_key, AES.MODE_CBC, first[-48:-32])
        decrypted = t.decrypt(first[:-48])
        out_file.write(decrypted)
        out_file.write(first[-48:])

        # Remaining pages
        for page in pages:
            t = AES.new(byte_key, AES.MODE_CBC, page[-48:-32])
            decrypted = t.decrypt(page[:-48])
            out_file.write(decrypted)
            out_file.write(page[-48:])

    return True, out_path


def find_user_dirs():
    """Find all WeChat user data directories."""
    if not os.path.exists(WECHAT_BASE_PATH):
        return []

    user_dirs = []
    for item in os.listdir(WECHAT_BASE_PATH):
        full_path = os.path.join(WECHAT_BASE_PATH, item)
        if os.path.isdir(full_path) and item not in ("all_users", "Backup"):
            db_storage = os.path.join(full_path, "db_storage")
            if os.path.exists(db_storage):
                user_dirs.append(full_path)
    return user_dirs


def decrypt_all_databases(key_hex, user_dir, output_dir):
    """Decrypt all database files in the user's db_storage directory."""
    db_storage = os.path.join(user_dir, "db_storage")
    results = {"success": [], "failed": []}

    # Find all .db files
    for root, dirs, files in os.walk(db_storage):
        for filename in files:
            if not filename.endswith(".db"):
                continue
            # Skip FTS (full-text search) databases - they use a different format
            if "_fts" in filename:
                continue

            db_path = os.path.join(root, filename)
            rel_path = os.path.relpath(db_path, db_storage)
            out_path = os.path.join(output_dir, "decrypted", rel_path)

            print(f"  Decrypting: {rel_path}...", end=" ")
            success, msg = decrypt_database(key_hex, db_path, out_path)

            if success:
                print("✓")
                results["success"].append(rel_path)
            else:
                print(f"✗ ({msg})")
                results["failed"].append((rel_path, msg))

    return results


class WeChatMacExporter:
    """Export chat history from decrypted WeChat Mac databases."""

    def __init__(self, decrypted_dir):
        self.decrypted_dir = decrypted_dir
        self.contacts = {}
        self.messages = []
        self._load_contacts()

    def _get_db_connection(self, rel_path):
        """Get a sqlite3 connection to a decrypted database."""
        db_path = os.path.join(self.decrypted_dir, rel_path)
        if not os.path.exists(db_path):
            return None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.DatabaseError:
            return None

    def _load_contacts(self):
        """Load contacts from the contact database."""
        conn = self._get_db_connection("contact/contact.db")
        if not conn:
            print("[-] Contact database not found or invalid")
            return

        try:
            cursor = conn.cursor()
            # Try to list tables first
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"[*] Contact DB tables: {tables}")

            # The Mac version may use different table/column names
            # Common table names: WCContact, Friend, Contact
            contact_tables = [t for t in tables if 'contact' in t.lower() or 'friend' in t.lower()]

            for table in contact_tables:
                try:
                    cursor.execute(f"PRAGMA table_info({table})")
                    columns = [(row[1], row[2]) for row in cursor.fetchall()]
                    print(f"    Table '{table}' columns: {[c[0] for c in columns]}")

                    # Try to read contacts
                    cursor.execute(f"SELECT * FROM {table} LIMIT 5")
                    rows = cursor.fetchall()
                    if rows:
                        print(f"    → {len(rows)} sample rows loaded")
                        # Parse based on available columns
                        col_names = [desc[0] for desc in cursor.description]
                        cursor.execute(f"SELECT * FROM {table}")
                        for row in cursor.fetchall():
                            row_dict = dict(zip(col_names, row))
                            # Try common field names
                            username = (row_dict.get('userName') or row_dict.get('UserName') or
                                       row_dict.get('username') or row_dict.get('m_nsUsrName') or '')
                            nickname = (row_dict.get('dbContactNickName') or row_dict.get('NickName') or
                                       row_dict.get('nickName') or row_dict.get('nickname') or
                                       row_dict.get('m_nsNickName') or '')
                            remark = (row_dict.get('dbContactRemark') or row_dict.get('Remark') or
                                     row_dict.get('remark') or row_dict.get('m_nsRemark') or '')
                            if username:
                                self.contacts[username] = {
                                    'username': username,
                                    'nickname': nickname,
                                    'remark': remark,
                                    'display_name': remark if remark else nickname,
                                }
                except sqlite3.OperationalError as e:
                    print(f"    Error reading {table}: {e}")
        except Exception as e:
            print(f"[-] Error loading contacts: {e}")
        finally:
            conn.close()

        print(f"[+] Loaded {len(self.contacts)} contacts")

    def list_conversations(self):
        """List all conversations with message counts."""
        conversations = {}

        # Scan all message databases
        msg_dir = os.path.join(self.decrypted_dir, "message")
        if not os.path.exists(msg_dir):
            print("[-] Message directory not found")
            return conversations

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "_fts" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()

                # Get tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                # Look for message tables
                msg_tables = [t for t in tables if 'msg' in t.lower() or 'message' in t.lower() or 'chat' in t.lower()]

                for table in msg_tables:
                    try:
                        cursor.execute(f"PRAGMA table_info({table})")
                        columns = [row[1] for row in cursor.fetchall()]

                        # Find the talker/session column
                        talker_col = None
                        for col_name in ['msgTalkerId', 'StrTalker', 'talker', 'sessionId', 'chatroomId',
                                         'msgFromId', 'ConStrRes1']:
                            if col_name in columns:
                                talker_col = col_name
                                break

                        if not talker_col:
                            # Try to infer from the table structure
                            continue

                        cursor.execute(f"SELECT {talker_col}, COUNT(*) as cnt FROM {table} GROUP BY {talker_col} ORDER BY cnt DESC")
                        for row in cursor.fetchall():
                            talker = row[0]
                            count = row[1]
                            if talker:
                                if talker not in conversations:
                                    conversations[talker] = {'count': 0, 'db_file': filename, 'table': table}
                                conversations[talker]['count'] += count
                    except sqlite3.OperationalError:
                        continue

                conn.close()
            except sqlite3.DatabaseError:
                continue

        return conversations

    def get_messages(self, username, limit=None):
        """Get all messages for a specific contact/group."""
        messages = []

        msg_dir = os.path.join(self.decrypted_dir, "message")
        if not os.path.exists(msg_dir):
            return messages

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "_fts" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                for table in tables:
                    if 'fts' in table.lower():
                        continue
                    try:
                        cursor.execute(f"PRAGMA table_info({table})")
                        columns = [row[1] for row in cursor.fetchall()]

                        # Identify columns
                        talker_col = None
                        content_col = None
                        time_col = None
                        type_col = None
                        sender_col = None

                        for col in columns:
                            col_lower = col.lower()
                            if col_lower in ('msgtalkerid', 'strtalker', 'talker', 'sessionid', 'constrres1'):
                                talker_col = col
                            elif col_lower in ('strcontent', 'content', 'msgcontent', 'message'):
                                content_col = col
                            elif col_lower in ('createtime', 'msgcreatetime', 'timestamp', 'ncreatetime'):
                                time_col = col
                            elif col_lower in ('type', 'msgtype', 'ntype'):
                                type_col = col
                            elif col_lower in ('issender', 'is_sender', 'msgissender'):
                                sender_col = col

                        if not talker_col or not content_col:
                            continue

                        # Build query
                        select_cols = [content_col]
                        if time_col:
                            select_cols.append(time_col)
                        if type_col:
                            select_cols.append(type_col)
                        if sender_col:
                            select_cols.append(sender_col)

                        sql = f"SELECT {','.join(select_cols)} FROM {table} WHERE {talker_col}=?"
                        if time_col:
                            sql += f" ORDER BY {time_col}"
                        if limit:
                            sql += f" LIMIT {limit}"

                        cursor.execute(sql, [username])
                        for row in cursor.fetchall():
                            msg = {'content': row[0] or ''}
                            idx = 1
                            if time_col:
                                timestamp = row[idx]
                                idx += 1
                                if isinstance(timestamp, (int, float)) and timestamp > 0:
                                    try:
                                        msg['time'] = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                                    except (ValueError, OSError):
                                        msg['time'] = str(timestamp)
                                else:
                                    msg['time'] = str(timestamp) if timestamp else ''
                            if type_col:
                                msg['type'] = row[idx]
                                idx += 1
                            if sender_col:
                                msg['is_sender'] = row[idx]
                                idx += 1
                            messages.append(msg)

                    except sqlite3.OperationalError:
                        continue

                conn.close()
            except sqlite3.DatabaseError:
                continue

        # Sort by time if available
        messages.sort(key=lambda m: m.get('time', ''))
        return messages

    def export_to_txt(self, username, output_path):
        """Export messages to a plain text file."""
        messages = self.get_messages(username)
        contact = self.contacts.get(username, {'display_name': username})
        display_name = contact.get('display_name', username)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"Chat History with: {display_name} ({username})\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 60 + "\n\n")

            for msg in messages:
                time_str = msg.get('time', '')
                is_sender = msg.get('is_sender', 0)
                content = msg.get('content', '')
                msg_type = msg.get('type', 1)

                sender = "Me" if is_sender else display_name

                # Handle different message types
                if msg_type == 1:  # Text
                    f.write(f"[{time_str}] {sender}: {content}\n")
                elif msg_type == 3:  # Image
                    f.write(f"[{time_str}] {sender}: [Image]\n")
                elif msg_type == 34:  # Voice
                    f.write(f"[{time_str}] {sender}: [Voice Message]\n")
                elif msg_type == 43:  # Video
                    f.write(f"[{time_str}] {sender}: [Video]\n")
                elif msg_type == 47:  # Emoji/Sticker
                    f.write(f"[{time_str}] {sender}: [Sticker]\n")
                elif msg_type == 48:  # Location
                    f.write(f"[{time_str}] {sender}: [Location] {content}\n")
                elif msg_type == 49:  # Link/File/App
                    f.write(f"[{time_str}] {sender}: [Link/File] {content}\n")
                elif msg_type == 10000:  # System
                    f.write(f"[{time_str}] --- {content} ---\n")
                else:
                    f.write(f"[{time_str}] {sender}: {content}\n")

        print(f"[+] Exported {len(messages)} messages to: {output_path}")
        return len(messages)

    def export_to_json(self, username, output_path):
        """Export messages to JSON format."""
        messages = self.get_messages(username)
        contact = self.contacts.get(username, {'display_name': username})

        export_data = {
            'contact': contact,
            'username': username,
            'export_time': datetime.now().isoformat(),
            'message_count': len(messages),
            'messages': messages,
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        print(f"[+] Exported {len(messages)} messages to: {output_path}")
        return len(messages)

    def export_to_html(self, username, output_path):
        """Export messages to an HTML file with chat-like UI."""
        messages = self.get_messages(username)
        contact = self.contacts.get(username, {'display_name': username})
        display_name = contact.get('display_name', username)

        html_template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chat with {display_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #ebebeb; }}
        .header {{ background: #ededed; padding: 15px 20px; text-align: center; border-bottom: 1px solid #d6d6d6; position: sticky; top: 0; z-index: 100; }}
        .header h1 {{ font-size: 17px; font-weight: 500; color: #333; }}
        .header .meta {{ font-size: 12px; color: #999; margin-top: 4px; }}
        .chat-container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
        .message {{ margin: 12px 0; display: flex; align-items: flex-start; }}
        .message.sent {{ flex-direction: row-reverse; }}
        .bubble {{ max-width: 65%; padding: 10px 14px; border-radius: 6px; font-size: 15px; line-height: 1.5; word-wrap: break-word; position: relative; }}
        .message.received .bubble {{ background: #fff; margin-left: 10px; }}
        .message.sent .bubble {{ background: #95ec69; margin-right: 10px; }}
        .avatar {{ width: 40px; height: 40px; border-radius: 4px; background: #ccc; display: flex; align-items: center; justify-content: center; font-size: 14px; color: #fff; flex-shrink: 0; }}
        .message.sent .avatar {{ background: #95ec69; color: #333; }}
        .message.received .avatar {{ background: #4a90d9; }}
        .time-divider {{ text-align: center; margin: 20px 0; color: #999; font-size: 12px; }}
        .system-msg {{ text-align: center; margin: 10px 0; color: #999; font-size: 13px; }}
        .media-placeholder {{ color: #666; font-style: italic; }}
        .timestamp {{ font-size: 11px; color: #999; margin-top: 4px; }}
        .message.sent .timestamp {{ text-align: right; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{display_name}</h1>
        <div class="meta">{message_count} messages | Exported {export_time}</div>
    </div>
    <div class="chat-container">
{messages_html}
    </div>
</body>
</html>"""

        messages_html = []
        last_date = None

        for msg in messages:
            time_str = msg.get('time', '')
            is_sender = msg.get('is_sender', 0)
            content = msg.get('content', '')
            msg_type = msg.get('type', 1)

            # Add date divider
            if time_str and len(time_str) >= 10:
                date_str = time_str[:10]
                if date_str != last_date:
                    messages_html.append(f'        <div class="time-divider">{date_str}</div>')
                    last_date = date_str

            # System messages
            if msg_type == 10000:
                messages_html.append(f'        <div class="system-msg">{self._html_escape(content)}</div>')
                continue

            direction = "sent" if is_sender else "received"
            avatar_text = "Me" if is_sender else display_name[:1]

            # Format content based on type
            if msg_type == 1:
                display_content = self._html_escape(content)
            elif msg_type == 3:
                display_content = '<span class="media-placeholder">📷 [Image]</span>'
            elif msg_type == 34:
                display_content = '<span class="media-placeholder">🎤 [Voice]</span>'
            elif msg_type == 43:
                display_content = '<span class="media-placeholder">🎬 [Video]</span>'
            elif msg_type == 47:
                display_content = '<span class="media-placeholder">😀 [Sticker]</span>'
            elif msg_type == 48:
                display_content = f'<span class="media-placeholder">📍 [Location]</span> {self._html_escape(content)}'
            elif msg_type == 49:
                display_content = f'<span class="media-placeholder">🔗 [Link/File]</span><br>{self._html_escape(content[:200])}'
            else:
                display_content = self._html_escape(content) if content else f'<span class="media-placeholder">[Type {msg_type}]</span>'

            time_display = time_str[11:16] if len(time_str) >= 16 else ''

            messages_html.append(f'''        <div class="message {direction}">
            <div class="avatar">{avatar_text}</div>
            <div>
                <div class="bubble">{display_content}</div>
                <div class="timestamp">{time_display}</div>
            </div>
        </div>''')

        html = html_template.format(
            display_name=self._html_escape(display_name),
            message_count=len(messages),
            export_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            messages_html='\n'.join(messages_html),
        )

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"[+] Exported {len(messages)} messages to: {output_path}")
        return len(messages)

    def export_to_csv(self, username, output_path):
        """Export messages to CSV format."""
        import csv
        messages = self.get_messages(username)
        contact = self.contacts.get(username, {'display_name': username})
        display_name = contact.get('display_name', username)

        with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Time', 'Sender', 'Type', 'Content'])

            for msg in messages:
                time_str = msg.get('time', '')
                is_sender = msg.get('is_sender', 0)
                content = msg.get('content', '')
                msg_type = msg.get('type', 1)

                sender = "Me" if is_sender else display_name
                type_name = {1: 'Text', 3: 'Image', 34: 'Voice', 43: 'Video',
                            47: 'Sticker', 48: 'Location', 49: 'Link/File',
                            10000: 'System'}.get(msg_type, f'Type_{msg_type}')

                writer.writerow([time_str, sender, type_name, content])

        print(f"[+] Exported {len(messages)} messages to: {output_path}")
        return len(messages)

    @staticmethod
    def _html_escape(text):
        """Escape HTML special characters."""
        return (text.replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;')
                    .replace('"', '&quot;')
                    .replace('\n', '<br>'))


def explore_db_schema(decrypted_dir):
    """Explore and print the schema of all decrypted databases."""
    print("\n" + "=" * 60)
    print("  Database Schema Explorer")
    print("=" * 60)

    for root, dirs, files in os.walk(decrypted_dir):
        for filename in sorted(files):
            if not filename.endswith('.db'):
                continue
            db_path = os.path.join(root, filename)
            rel_path = os.path.relpath(db_path, decrypted_dir)

            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                if tables:
                    print(f"\n📁 {rel_path}")
                    for table in tables:
                        cursor.execute(f"PRAGMA table_info({table})")
                        columns = cursor.fetchall()
                        cursor.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cursor.fetchone()[0]
                        col_names = [col[1] for col in columns]
                        print(f"  📋 {table} ({count} rows): {', '.join(col_names)}")

                conn.close()
            except (sqlite3.DatabaseError, sqlite3.OperationalError):
                continue


def main():
    parser = argparse.ArgumentParser(
        description="WeChat Mac Database Decryptor & Chat History Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decrypt all databases:
  python3 decrypt_and_export.py --key abc123...def456 decrypt

  # List all conversations:
  python3 decrypt_and_export.py --key abc123...def456 list

  # Export a specific chat to HTML:
  python3 decrypt_and_export.py --key abc123...def456 export --contact wxid_xxx --format html

  # Export all chats to text:
  python3 decrypt_and_export.py --key abc123...def456 export --all --format txt

  # Explore database schema:
  python3 decrypt_and_export.py --key abc123...def456 schema
"""
    )

    parser.add_argument("--key", type=str, help="64-character hex encryption key")
    parser.add_argument("--key-file", type=str, help="Path to file containing the key")
    parser.add_argument("--user-dir", type=str, help="Path to WeChat user directory (auto-detected if not specified)")
    parser.add_argument("--output", "-o", type=str, default="./wechat_export",
                       help="Output directory (default: ./wechat_export)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Decrypt command
    decrypt_parser = subparsers.add_parser("decrypt", help="Decrypt all databases")

    # List command
    list_parser = subparsers.add_parser("list", help="List all conversations")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export chat history")
    export_parser.add_argument("--contact", type=str, help="Contact username/wxid to export")
    export_parser.add_argument("--all", action="store_true", help="Export all conversations")
    export_parser.add_argument("--format", choices=["txt", "html", "json", "csv"], default="txt",
                              help="Export format (default: txt)")
    export_parser.add_argument("--limit", type=int, help="Limit number of messages per conversation")

    # Schema command
    schema_parser = subparsers.add_parser("schema", help="Explore database schema")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Get the key
    key_hex = None
    if args.key:
        key_hex = args.key
    elif args.key_file:
        with open(args.key_file, 'r') as f:
            key_hex = f.read().strip()
    else:
        # Try to find saved key
        user_dirs = find_user_dirs()
        for ud in user_dirs:
            key_file = os.path.join(ud, "db_key.txt")
            if os.path.exists(key_file):
                with open(key_file, 'r') as f:
                    key_hex = f.read().strip()
                print(f"[*] Using saved key from: {key_file}")
                break

    if not key_hex:
        print("[-] No key provided. Use --key or --key-file, or run extract_key.py first.")
        sys.exit(1)

    if len(key_hex) != 64:
        print(f"[-] Key must be 64 hex characters, got {len(key_hex)}")
        sys.exit(1)

    # Find user directory
    if args.user_dir:
        user_dir = args.user_dir
    else:
        user_dirs = find_user_dirs()
        if not user_dirs:
            print("[-] No WeChat user data found")
            sys.exit(1)
        user_dir = user_dirs[0]
        print(f"[*] Using WeChat data: {os.path.basename(user_dir)}")

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)
    decrypted_dir = os.path.join(output_dir, "decrypted")

    # Decrypt if needed
    if args.command == "decrypt" or not os.path.exists(decrypted_dir):
        print("\n[*] Decrypting databases...")
        results = decrypt_all_databases(key_hex, user_dir, output_dir)
        print(f"\n[+] Decryption complete: {len(results['success'])} succeeded, {len(results['failed'])} failed")

        if args.command == "decrypt":
            return

    # Check if decrypted dir exists
    if not os.path.exists(decrypted_dir):
        print("[-] No decrypted databases found. Run 'decrypt' command first.")
        sys.exit(1)

    if args.command == "schema":
        explore_db_schema(decrypted_dir)
        return

    # Initialize exporter
    exporter = WeChatMacExporter(decrypted_dir)

    if args.command == "list":
        print("\n[*] Listing conversations...")
        conversations = exporter.list_conversations()

        if not conversations:
            print("[-] No conversations found")
            return

        print(f"\n{'Username':<40} {'Display Name':<20} {'Messages':>10}")
        print("-" * 72)

        sorted_convos = sorted(conversations.items(), key=lambda x: x[1]['count'], reverse=True)
        for username, info in sorted_convos[:50]:
            contact = exporter.contacts.get(username, {})
            display = contact.get('display_name', '')[:20]
            print(f"{username:<40} {display:<20} {info['count']:>10}")

        if len(sorted_convos) > 50:
            print(f"\n... and {len(sorted_convos) - 50} more conversations")

    elif args.command == "export":
        export_dir = os.path.join(output_dir, "exports")
        os.makedirs(export_dir, exist_ok=True)

        if args.contact:
            # Export single contact
            contact_id = args.contact
            ext = {'txt': '.txt', 'html': '.html', 'json': '.json', 'csv': '.csv'}[args.format]
            safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in contact_id)
            out_path = os.path.join(export_dir, f"{safe_name}{ext}")

            if args.format == 'txt':
                exporter.export_to_txt(contact_id, out_path)
            elif args.format == 'html':
                exporter.export_to_html(contact_id, out_path)
            elif args.format == 'json':
                exporter.export_to_json(contact_id, out_path)
            elif args.format == 'csv':
                exporter.export_to_csv(contact_id, out_path)

        elif args.all:
            # Export all conversations
            conversations = exporter.list_conversations()
            print(f"\n[*] Exporting {len(conversations)} conversations...")

            for username, info in sorted(conversations.items(), key=lambda x: x[1]['count'], reverse=True):
                if info['count'] < 1:
                    continue
                ext = {'txt': '.txt', 'html': '.html', 'json': '.json', 'csv': '.csv'}[args.format]
                safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in username)
                out_path = os.path.join(export_dir, f"{safe_name}{ext}")

                try:
                    if args.format == 'txt':
                        exporter.export_to_txt(username, out_path)
                    elif args.format == 'html':
                        exporter.export_to_html(username, out_path)
                    elif args.format == 'json':
                        exporter.export_to_json(username, out_path)
                    elif args.format == 'csv':
                        exporter.export_to_csv(username, out_path)
                except Exception as e:
                    print(f"[-] Error exporting {username}: {e}")
        else:
            print("[-] Specify --contact <username> or --all")
            sys.exit(1)


if __name__ == "__main__":
    main()
