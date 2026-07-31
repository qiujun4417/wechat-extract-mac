#!/bin/bash
# WeChat Mac Key Extraction - Step by Step
#
# Run this in your iTerm terminal:
#   cd ~/project/personal/wechat-extract-mac
#   bash run_extract.sh

set -e

echo "============================================================"
echo "  WeChat Mac Key Extractor"
echo "============================================================"
echo ""

# Step 0: Check prerequisites
PID=$(pgrep -x WeChat 2>/dev/null || true)
if [ -z "$PID" ]; then
    echo "❌ WeChat is not running. Please start WeChat and log in first."
    exit 1
fi
echo "✓ WeChat is running (PID: $PID)"

# Find database
WECHAT_DATA="$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
USER_DIR=$(find "$WECHAT_DATA" -maxdepth 1 -type d ! -name "all_users" ! -name "Backup" ! -name "xwechat_files" 2>/dev/null | head -1)

if [ -z "$USER_DIR" ]; then
    echo "❌ No WeChat user data found"
    exit 1
fi

DB_PATH="$USER_DIR/db_storage/contact/contact.db"
if [ ! -f "$DB_PATH" ]; then
    DB_PATH="$USER_DIR/db_storage/message/message_0.db"
fi

if [ ! -f "$DB_PATH" ]; then
    echo "❌ No database file found"
    exit 1
fi
echo "✓ Database: $DB_PATH"
echo ""

# Step 1: Enable Developer Mode (required for lldb to attach)
DEV_STATUS=$(DevToolsSecurity -status 2>&1)
if echo "$DEV_STATUS" | grep -q "disabled"; then
    echo "⚠️  Developer Mode is disabled. Enabling it now (requires password)..."
    sudo DevToolsSecurity -enable
    if [ $? -ne 0 ]; then
        echo "❌ Failed to enable Developer Mode."
        echo "   Please run manually: sudo DevToolsSecurity -enable"
        exit 1
    fi
    echo "✓ Developer Mode enabled"
else
    echo "✓ Developer Mode already enabled"
fi
echo ""

# Step 2: Compile the key finder if needed
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEY_FINDER="$SCRIPT_DIR/wechat_key_finder"

if [ ! -f "$KEY_FINDER" ]; then
    echo "Compiling key finder..."
    clang -o "$KEY_FINDER" "$SCRIPT_DIR/key_finder.c" -framework Security -framework CoreFoundation 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ Compilation failed. Install Xcode Command Line Tools:"
        echo "   xcode-select --install"
        exit 1
    fi
    # Sign with debug entitlements
    cat > /tmp/debug_entitlements.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.debugger</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
</dict>
</plist>
PLIST
    codesign --force --sign - --entitlements /tmp/debug_entitlements.plist "$KEY_FINDER" 2>/dev/null
fi
echo "✓ Key finder ready"
echo ""

# Step 3: Try Method 1 - Direct memory scan with sudo
echo "🔑 Method 1: Direct memory scan (requires password)..."
echo ""
KEY=$(sudo "$KEY_FINDER" "$PID" "$DB_PATH" 2>/tmp/wechat_keyfinder_err.txt)
KEYFINDER_EXIT=$?

if [ $KEYFINDER_EXIT -eq 0 ] && [ -n "$KEY" ] && [ ${#KEY} -eq 65 ]; then
    # trim newline
    KEY=$(echo "$KEY" | tr -d '\n\r')
fi

if [ $KEYFINDER_EXIT -eq 0 ] && [ ${#KEY} -eq 64 ]; then
    echo ""
    echo "============================================================"
    echo "  ✅ SUCCESS! Key extracted:"
    echo "  $KEY"
    echo "============================================================"
    echo "$KEY" > "$SCRIPT_DIR/db_key.txt"
    echo ""
    echo "Key saved to: $SCRIPT_DIR/db_key.txt"
    echo ""
    echo "Next steps:"
    echo "  cd $SCRIPT_DIR"
    echo "  python3 decrypt_and_export.py --key-file db_key.txt decrypt"
    echo "  python3 decrypt_and_export.py --key-file db_key.txt list"
    echo "  python3 decrypt_and_export.py --key-file db_key.txt export --all --format html"
    exit 0
fi

# Method 1 failed, show error
echo "⚠️  Method 1 failed:"
cat /tmp/wechat_keyfinder_err.txt
echo ""

# Step 4: Try Method 2 - lldb
echo "🔑 Method 2: Using lldb..."
echo ""

# Generate the lldb Python script with the DB path baked in
python3 "$SCRIPT_DIR/lldb_extract_key.py" <<< "q" 2>/dev/null || true

# Try lldb
echo "Attaching lldb to WeChat (PID: $PID)..."
LLDB_OUTPUT=$(lldb --batch -o "process attach --pid $PID" -o "command script import /tmp/wechat_key_extract_lldb.py" -o "wechat_find_key" -o "detach" -o "quit" 2>&1)

if echo "$LLDB_OUTPUT" | grep -q "KEY_FOUND\|KEY FOUND"; then
    KEY=$(echo "$LLDB_OUTPUT" | grep -oE "[0-9a-f]{64}" | head -1)
    if [ ${#KEY} -eq 64 ]; then
        echo ""
        echo "============================================================"
        echo "  ✅ SUCCESS! Key extracted:"
        echo "  $KEY"
        echo "============================================================"
        echo "$KEY" > "$SCRIPT_DIR/db_key.txt"
        echo ""
        echo "Key saved to: $SCRIPT_DIR/db_key.txt"
        exit 0
    fi
fi

# Both methods failed
echo ""
echo "============================================================"
echo "  ❌ Automatic extraction failed."
echo ""
echo "  This is usually because SIP blocks memory access."
echo ""
echo "  OPTION A: Run lldb manually with sudo:"
echo "    sudo lldb -p $PID"
echo "    (lldb) command script import /tmp/wechat_key_extract_lldb.py"
echo "    (lldb) wechat_find_key"
echo "    (lldb) detach"
echo "    (lldb) quit"
echo ""
echo "  OPTION B: Temporarily disable SIP (reboot required):"
echo "    1. Restart Mac, hold Cmd+R for Recovery Mode"
echo "    2. Open Terminal from the menu"
echo "    3. Run: csrutil disable"
echo "    4. Restart normally"
echo "    5. Run this script again"
echo "    6. Re-enable SIP: csrutil enable (in Recovery Mode)"
echo ""
echo "  OPTION C: Use an iOS backup approach instead:"
echo "    - Back up your iPhone (unencrypted) to this Mac"
echo "    - Use a tool like iMazing to extract WeChat data"
echo "    - Those databases are NOT encrypted"
echo "============================================================"
exit 1
