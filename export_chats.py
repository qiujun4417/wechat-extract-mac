#!/usr/bin/env python3
"""
WeChat Mac Chat History Exporter

Exports chat history from decrypted WeChat Mac 4.x databases.
Use after running: sudo python3 wcdb_key_tool_macos.py extract --decrypt

Usage:
    python3 export_chats.py list                    # List all conversations
    python3 export_chats.py export <username>       # Export one chat to txt
    python3 export_chats.py export <username> --format html
    python3 export_chats.py export --all            # Export all chats
    python3 export_chats.py search <keyword>        # Search messages
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Default decrypted DB directory
DEFAULT_DB_DIR = os.path.expanduser("~/project/personal/wcdb-key-tool/decrypted")
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/project/personal/wechat-export")


class WeChatExporter:
    def __init__(self, db_dir=DEFAULT_DB_DIR):
        self.db_dir = db_dir
        self.contacts = {}
        self.name2id = {}  # username -> table hash mapping
        self._load_contacts()
        self._load_name2id()

    def _connect(self, rel_path):
        db_path = os.path.join(self.db_dir, rel_path)
        if not os.path.exists(db_path):
            return None
        return sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)

    def _load_contacts(self):
        conn = self._connect("contact/contact.db")
        if not conn:
            print("⚠️  contact.db not found")
            return
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT username, nick_name, remark, alias, local_type
                FROM contact WHERE nick_name != ''
            """)
            for row in cursor.fetchall():
                username, nickname, remark, alias, local_type = row
                self.contacts[username] = {
                    'username': username,
                    'nickname': nickname or '',
                    'remark': remark or '',
                    'alias': alias or '',
                    'display_name': remark if remark else nickname,
                    'is_chatroom': '@chatroom' in username,
                }
        except sqlite3.OperationalError as e:
            print(f"⚠️  Error loading contacts: {e}")
        conn.close()
        print(f"✓ Loaded {len(self.contacts)} contacts")

    def _load_name2id(self):
        """Load username-to-table mappings from all message DBs."""
        msg_dir = os.path.join(self.db_dir, "message")
        if not os.path.exists(msg_dir):
            return

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "fts" in filename or "resource" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
                cursor = conn.cursor()

                # Get all Msg_ tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                msg_tables = [r[0] for r in cursor.fetchall()]

                # Get Name2Id mapping
                cursor.execute("SELECT user_name FROM Name2Id")
                for (username,) in cursor.fetchall():
                    # The table name is Msg_ + hash of username
                    # We need to find which table corresponds to which username
                    # by checking which table has messages with real_sender_id matching
                    if username not in self.name2id:
                        self.name2id[username] = {'db': filename, 'tables': msg_tables}

                conn.close()
            except (sqlite3.DatabaseError, sqlite3.OperationalError):
                continue

    def _get_messages_for_user(self, username):
        """Get all messages for a username across all message DBs."""
        messages = []
        msg_dir = os.path.join(self.db_dir, "message")

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "fts" in filename or "resource" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
                cursor = conn.cursor()

                # Find the table for this username
                # WeChat Mac 4.x uses MD5 hash of username as table suffix
                import hashlib
                table_hash = hashlib.md5(username.encode()).hexdigest()
                table_name = f"Msg_{table_hash}"

                # Check if table exists
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if not cursor.fetchone():
                    conn.close()
                    continue

                # Get messages
                cursor.execute(f"""
                    SELECT local_id, local_type, create_time, message_content,
                           real_sender_id, WCDB_CT_message_content
                    FROM [{table_name}]
                    ORDER BY create_time ASC
                """)

                for row in cursor.fetchall():
                    local_id, msg_type, create_time, content, sender_id, ct_type = row

                    # Decode content if compressed
                    if isinstance(content, bytes):
                        if ct_type == 0:
                            content = content.decode('utf-8', errors='replace')
                        else:
                            content = f"[Compressed message type {ct_type}]"

                    messages.append({
                        'id': local_id,
                        'type': msg_type,
                        'time': datetime.fromtimestamp(create_time).strftime('%Y-%m-%d %H:%M:%S') if create_time else '',
                        'timestamp': create_time,
                        'content': content or '',
                        'sender_id': sender_id,
                    })

                conn.close()
            except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
                continue

        messages.sort(key=lambda m: m.get('timestamp', 0))
        return messages

    def list_conversations(self, top_n=50):
        """List conversations with message counts."""
        conversations = {}
        msg_dir = os.path.join(self.db_dir, "message")

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "fts" in filename or "resource" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
                cursor = conn.cursor()

                # Get Name2Id
                cursor.execute("SELECT user_name, is_session FROM Name2Id")
                name2id_rows = cursor.fetchall()

                for username, is_session in name2id_rows:
                    import hashlib
                    table_hash = hashlib.md5(username.encode()).hexdigest()
                    table_name = f"Msg_{table_hash}"

                    try:
                        cursor.execute(f"SELECT COUNT(*), MAX(create_time) FROM [{table_name}]")
                        result = cursor.fetchone()
                        if result and result[0] > 0:
                            count, last_time = result
                            if username not in conversations:
                                conversations[username] = {'count': 0, 'last_time': 0}
                            conversations[username]['count'] += count
                            conversations[username]['last_time'] = max(
                                conversations[username]['last_time'], last_time or 0
                            )
                    except sqlite3.OperationalError:
                        continue

                conn.close()
            except (sqlite3.DatabaseError, sqlite3.OperationalError):
                continue

        # Sort by message count
        sorted_convos = sorted(conversations.items(), key=lambda x: x[1]['count'], reverse=True)

        print(f"\n{'Username':<40} {'Name':<20} {'Messages':>10} {'Last Active'}")
        print("-" * 90)

        for username, info in sorted_convos[:top_n]:
            contact = self.contacts.get(username, {})
            display = contact.get('display_name', '')[:20]
            last_time = datetime.fromtimestamp(info['last_time']).strftime('%Y-%m-%d') if info['last_time'] else ''
            print(f"{username:<40} {display:<20} {info['count']:>10} {last_time}")

        print(f"\nTotal: {len(sorted_convos)} conversations")
        return sorted_convos

    def export_chat(self, username, output_dir=DEFAULT_OUTPUT_DIR, fmt='txt'):
        """Export a single chat."""
        os.makedirs(output_dir, exist_ok=True)
        messages = self._get_messages_for_user(username)

        if not messages:
            print(f"❌ No messages found for: {username}")
            return 0

        contact = self.contacts.get(username, {'display_name': username})
        display_name = contact.get('display_name', username)
        safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in (display_name or username))

        if fmt == 'txt':
            return self._export_txt(messages, username, display_name, safe_name, output_dir)
        elif fmt == 'html':
            return self._export_html(messages, username, display_name, safe_name, output_dir)
        elif fmt == 'json':
            return self._export_json(messages, username, display_name, safe_name, output_dir)
        elif fmt == 'csv':
            return self._export_csv(messages, username, display_name, safe_name, output_dir)

    def _get_type_label(self, msg_type):
        types = {
            1: '', 3: '[Image]', 34: '[Voice]', 43: '[Video]',
            47: '[Sticker]', 48: '[Location]', 49: '[Link/File]',
            50: '[VoIP]', 10000: '[System]', 10002: '[System]',
            42: '[Contact Card]', 62: '[Short Video]',
        }
        return types.get(msg_type, f'[Type:{msg_type}]')

    def _export_txt(self, messages, username, display_name, safe_name, output_dir):
        out_path = os.path.join(output_dir, f"{safe_name}.txt")
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"Chat History: {display_name} ({username})\n")
            f.write(f"Messages: {len(messages)}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")

            for msg in messages:
                time_str = msg['time']
                content = msg['content']
                msg_type = msg['type']
                sender_id = msg['sender_id']

                type_label = self._get_type_label(msg_type)

                if msg_type == 10000 or msg_type == 10002:
                    f.write(f"[{time_str}] --- {content} ---\n")
                else:
                    # sender_id: 0 = self, others = contact
                    sender = "Me" if sender_id == 0 else display_name
                    prefix = f"[{time_str}] {sender}: "
                    if type_label and msg_type != 1:
                        f.write(f"{prefix}{type_label} {content}\n")
                    else:
                        f.write(f"{prefix}{content}\n")

        print(f"✓ Exported {len(messages)} messages → {out_path}")
        return len(messages)

    def _export_html(self, messages, username, display_name, safe_name, output_dir):
        out_path = os.path.join(output_dir, f"{safe_name}.html")

        msgs_html = []
        last_date = None
        for msg in messages:
            time_str = msg['time']
            content = msg['content'] or ''
            msg_type = msg['type']
            sender_id = msg['sender_id']

            # Date divider
            if time_str and len(time_str) >= 10:
                date_str = time_str[:10]
                if date_str != last_date:
                    msgs_html.append(f'<div class="date">{date_str}</div>')
                    last_date = date_str

            if msg_type in (10000, 10002):
                msgs_html.append(f'<div class="sys">{_esc(content)}</div>')
                continue

            direction = "sent" if sender_id == 0 else "recv"
            type_label = self._get_type_label(msg_type)
            display = _esc(content) if msg_type == 1 else f'<em>{type_label}</em> {_esc(content)[:200]}'
            time_short = time_str[11:16] if len(time_str) >= 16 else ''

            msgs_html.append(f'''<div class="msg {direction}">
  <div class="bubble">{display}</div>
  <div class="ts">{time_short}</div>
</div>''')

        html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_esc(display_name)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#ebebeb;padding:20px}}
.hdr{{text-align:center;padding:15px;background:#ededed;border-radius:8px;margin-bottom:20px}}
.hdr h1{{font-size:18px}} .hdr p{{font-size:12px;color:#999}}
.chat{{max-width:700px;margin:0 auto}}
.msg{{margin:10px 0;display:flex}} .msg.sent{{flex-direction:row-reverse}}
.bubble{{max-width:60%;padding:10px 14px;border-radius:8px;font-size:15px;line-height:1.5;word-break:break-all}}
.recv .bubble{{background:#fff}} .sent .bubble{{background:#95ec69}}
.ts{{font-size:11px;color:#999;margin-top:4px;padding:0 8px}}
.msg.sent .ts{{text-align:right}}
.date{{text-align:center;color:#999;font-size:12px;margin:15px 0}}
.sys{{text-align:center;color:#999;font-size:13px;margin:8px 0}}
em{{color:#666;font-style:italic}}
</style></head><body>
<div class="hdr"><h1>{_esc(display_name)}</h1><p>{len(messages)} messages | {username}</p></div>
<div class="chat">
{chr(10).join(msgs_html)}
</div></body></html>'''

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"✓ Exported {len(messages)} messages → {out_path}")
        return len(messages)

    def _export_json(self, messages, username, display_name, safe_name, output_dir):
        out_path = os.path.join(output_dir, f"{safe_name}.json")
        data = {
            'username': username,
            'display_name': display_name,
            'export_time': datetime.now().isoformat(),
            'message_count': len(messages),
            'messages': messages,
        }
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✓ Exported {len(messages)} messages → {out_path}")
        return len(messages)

    def _export_csv(self, messages, username, display_name, safe_name, output_dir):
        out_path = os.path.join(output_dir, f"{safe_name}.csv")
        with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Time', 'Sender', 'Type', 'Content'])
            for msg in messages:
                sender = "Me" if msg['sender_id'] == 0 else display_name
                type_name = self._get_type_label(msg['type']) or 'Text'
                writer.writerow([msg['time'], sender, type_name, msg['content']])
        print(f"✓ Exported {len(messages)} messages → {out_path}")
        return len(messages)

    def search(self, keyword, limit=50):
        """Search all messages for a keyword."""
        results = []
        msg_dir = os.path.join(self.db_dir, "message")

        for filename in sorted(os.listdir(msg_dir)):
            if not filename.startswith("message_") or not filename.endswith(".db"):
                continue
            if "fts" in filename or "resource" in filename:
                continue

            db_path = os.path.join(msg_dir, filename)
            try:
                conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
                cursor = conn.cursor()

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                tables = [r[0] for r in cursor.fetchall()]

                for table in tables:
                    try:
                        cursor.execute(f"""
                            SELECT message_content, create_time, local_type
                            FROM [{table}]
                            WHERE message_content LIKE ? AND local_type = 1
                            ORDER BY create_time DESC
                            LIMIT 10
                        """, (f"%{keyword}%",))

                        for content, ctime, mtype in cursor.fetchall():
                            if isinstance(content, bytes):
                                content = content.decode('utf-8', errors='replace')
                            time_str = datetime.fromtimestamp(ctime).strftime('%Y-%m-%d %H:%M:%S') if ctime else ''
                            results.append((time_str, content, table))
                    except sqlite3.OperationalError:
                        continue

                conn.close()
            except (sqlite3.DatabaseError, sqlite3.OperationalError):
                continue

            if len(results) >= limit:
                break

        results.sort(key=lambda x: x[0], reverse=True)
        print(f"\n🔍 Found {len(results)} messages containing '{keyword}':\n")
        for time_str, content, table in results[:limit]:
            content_short = content[:100].replace('\n', ' ')
            print(f"  [{time_str}] {content_short}")

        return results


def _esc(text):
    if not text:
        return ''
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;').replace('\n', '<br>'))


def main():
    parser = argparse.ArgumentParser(description="WeChat Mac Chat History Exporter")
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR, help="Path to decrypted DB directory")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT_DIR, help="Output directory")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all conversations")

    export_p = sub.add_parser("export", help="Export chat history")
    export_p.add_argument("username", nargs='?', help="Username/wxid to export")
    export_p.add_argument("--all", action="store_true", help="Export all conversations")
    export_p.add_argument("--format", "-f", choices=['txt', 'html', 'json', 'csv'], default='txt')
    export_p.add_argument("--min-messages", type=int, default=1, help="Min messages to export (for --all)")

    search_p = sub.add_parser("search", help="Search messages")
    search_p.add_argument("keyword", help="Keyword to search")
    search_p.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    exporter = WeChatExporter(db_dir=args.db_dir)

    if args.command == "list":
        exporter.list_conversations()

    elif args.command == "export":
        if args.all:
            convos = exporter.list_conversations(top_n=9999)
            os.makedirs(args.output, exist_ok=True)
            total = 0
            for username, info in convos:
                if info['count'] >= args.min_messages:
                    total += exporter.export_chat(username, args.output, args.format)
            print(f"\n✅ Exported {total} total messages")
        elif args.username:
            exporter.export_chat(args.username, args.output, args.format)
        else:
            print("Specify a username or use --all")

    elif args.command == "search":
        exporter.search(args.keyword, args.limit)


if __name__ == "__main__":
    main()
