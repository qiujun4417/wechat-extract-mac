# WeChat Mac Chat History Extractor

A pure Python tool to extract and export WeChat chat history on macOS.

## Overview

This tool decrypts the WeChat Mac encrypted SQLCipher databases and exports chat history in multiple formats (TXT, HTML, JSON, CSV).

**Tested with:** WeChat Mac 4.1.11

## How It Works

WeChat for Mac 4.x encrypts its local SQLite databases using SQLCipher (AES-256-CBC with HMAC-SHA1, 64000 PBKDF2 iterations). This tool:

1. **Extracts the encryption key** from the running WeChat process memory
2. **Decrypts** all database files (contacts, messages, etc.)
3. **Exports** chat history in your preferred format

## Prerequisites

```bash
pip3 install pycryptodome
```

## Usage

### Step 1: Extract the Encryption Key

The key must be extracted while WeChat is **running and logged in**.

```bash
# Method 1: Automated (may need permissions)
python3 extract_key.py

# Method 2: LLDB (more reliable, needs Terminal in Developer Tools)
python3 lldb_extract_key.py
```

#### Manual LLDB Method (most reliable):

```bash
# 1. Find WeChat PID
pgrep -x WeChat

# 2. Attach lldb (use sudo if needed)
sudo lldb -p <PID>

# 3. In lldb, load the extraction script
(lldb) command script import /tmp/wechat_key_extract_lldb.py
(lldb) wechat_find_key

# 4. Detach and quit
(lldb) detach
(lldb) quit
```

The key (64 hex characters) will be saved to `db_key.txt`.

### Step 2: Decrypt Databases

```bash
python3 decrypt_and_export.py --key <YOUR_64_CHAR_HEX_KEY> decrypt
```

### Step 3: List Conversations

```bash
python3 decrypt_and_export.py --key <KEY> list
```

### Step 4: Export Chat History

```bash
# Export a specific contact's chat to HTML
python3 decrypt_and_export.py --key <KEY> export --contact wxid_xxxxx --format html

# Export all chats to text files
python3 decrypt_and_export.py --key <KEY> export --all --format txt

# Export to JSON (good for further processing)
python3 decrypt_and_export.py --key <KEY> export --contact wxid_xxxxx --format json

# Export to CSV
python3 decrypt_and_export.py --key <KEY> export --all --format csv
```

### Step 5: Explore Database Schema (optional)

```bash
python3 decrypt_and_export.py --key <KEY> schema
```

## Output Formats

| Format | Description |
|--------|-------------|
| `txt`  | Plain text, one message per line |
| `html` | Beautiful chat-like UI (WeChat style) |
| `json` | Structured JSON for programmatic use |
| `csv`  | Spreadsheet-compatible |

## File Structure

```
wechat-extract-mac/
├── README.md                  # This file
├── extract_key.py             # Automated key extraction (memory scan)
├── lldb_extract_key.py        # LLDB-based key extraction (more reliable)
├── decrypt_and_export.py      # Main tool: decrypt + export
├── db_key.txt                 # Saved key (created after extraction)
└── wechat_export/             # Output directory (created on export)
    ├── decrypted/             # Decrypted SQLite databases
    │   ├── contact/
    │   ├── message/
    │   └── ...
    └── exports/               # Exported chat files
        ├── wxid_xxx.html
        ├── wxid_yyy.txt
        └── ...
```

## WeChat Mac Data Location

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<username>_<hash>/
├── db_storage/
│   ├── contact/contact.db       # Contact list
│   ├── message/message_0.db     # Chat messages (sharded)
│   ├── message/message_1.db
│   ├── session/                  # Session/conversation list
│   └── ...
├── msg/
│   ├── attach/                   # Attachments (images, files)
│   ├── video/                    # Video messages
│   └── ...
└── cache/                        # Cached media
```

## Troubleshooting

### "Permission denied" or "task_for_pid failed"
- Enable Terminal in **System Preferences → Privacy & Security → Developer Tools**
- Or run with `sudo`
- If SIP is enabled, you may need to use the lldb method

### "Key verification failed"
- Make sure WeChat is logged in when extracting the key
- The key changes if you log out and log back in
- Try extracting the key again

### Empty exports / No messages found
- Run the `schema` command first to check if decryption worked
- WeChat Mac 4.x shards messages across multiple database files (message_0.db through message_N.db)
- The table/column names may differ between WeChat versions

### lldb won't attach
- Disable SIP temporarily: reboot into Recovery Mode (Cmd+R), run `csrutil disable`
- **Remember to re-enable SIP after**: `csrutil enable`

## Technical Details

### Encryption Scheme
- Algorithm: AES-256-CBC (same as SQLCipher)
- Page size: 4096 bytes
- KDF: PBKDF2-HMAC-SHA1, 64000 iterations
- Each page has its own IV (last 48 bytes: 16B IV + 20B HMAC + 12B padding)
- First 16 bytes of the file are the salt

### Key Storage
The raw 32-byte key exists in the WeChat process memory (heap). It's used to call SQLCipher's `sqlite3_key()` when opening databases.

## Disclaimer

This tool is for **personal data backup purposes only**. You should only use it to extract your own chat history. The author is not responsible for any misuse.

## Credits

- Inspired by [WeChatMsg](https://github.com/LC044/WeChatMsg) (Windows version)
- SQLCipher encryption scheme documentation from [PyWxDump](https://github.com/xaoyaoo/PyWxDump)
