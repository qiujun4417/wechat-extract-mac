#!/bin/bash
# WeChat Mac Key Extractor - Hook Method (v2)
# Works with SIP enabled by creating an ad-hoc signed copy without hardened runtime
#
# This will briefly launch a copy of WeChat to capture the encryption key.

set -e

echo "============================================================"
echo "  WeChat Key Extractor (Hook Method v2)"
echo "  Works with SIP enabled!"
echo "============================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_LIB="/tmp/wechat_hook.dylib"
KEY_FILE="/tmp/wechat_db_key.txt"
WECHAT_COPY="/tmp/WeChat_hooked"

# Step 1: Compile hook if needed
if [ ! -f "$HOOK_LIB" ]; then
    echo "[*] Compiling hook library..."
    cat > /tmp/wechat_hook.c << 'CEOF'
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include <stdlib.h>

typedef int (*orig_fn_t)(void*, const void*, int);
typedef int (*orig_fn_v2_t)(void*, const char*, const void*, int);

static void log_key(const char *func, const char *db_name, const void *pKey, int nKey) {
    FILE *f = fopen("/tmp/wechat_db_key.txt", "a");
    if (f) {
        fprintf(f, "%s db=%s nKey=%d key=", func, db_name ? db_name : "main", nKey);
        const unsigned char *k = (const unsigned char*)pKey;
        for (int i = 0; i < nKey && i < 64; i++) fprintf(f, "%02x", k[i]);
        fprintf(f, "\n");
        fflush(f);
        fclose(f);
    }
}

int sqlite3_key(void *db, const void *pKey, int nKey) {
    log_key("sqlite3_key", NULL, pKey, nKey);
    orig_fn_t orig = (orig_fn_t)dlsym(RTLD_NEXT, "sqlite3_key");
    return orig ? orig(db, pKey, nKey) : 0;
}

int sqlite3_key_v2(void *db, const char *zDbName, const void *pKey, int nKey) {
    log_key("sqlite3_key_v2", zDbName, pKey, nKey);
    orig_fn_v2_t orig = (orig_fn_v2_t)dlsym(RTLD_NEXT, "sqlite3_key_v2");
    return orig ? orig(db, zDbName, pKey, nKey) : 0;
}

// Also hook WCDB's key function if it exists
int sqlite3_rekey(void *db, const void *pKey, int nKey) {
    log_key("sqlite3_rekey", NULL, pKey, nKey);
    orig_fn_t orig = (orig_fn_t)dlsym(RTLD_NEXT, "sqlite3_rekey");
    return orig ? orig(db, pKey, nKey) : 0;
}

__attribute__((constructor)) void hook_init(void) {
    FILE *f = fopen("/tmp/wechat_db_key.txt", "a");
    if (f) { fprintf(f, "=== Hook loaded into process! ===\n"); fclose(f); }
}
CEOF
    clang -dynamiclib -o "$HOOK_LIB" /tmp/wechat_hook.c -ldl 2>&1
    echo "✓ Hook compiled"
fi

# Step 2: Create a modified copy of the WeChat binary only (not the whole .app)
echo "[*] Preparing hooked WeChat binary..."
rm -rf "$WECHAT_COPY"
mkdir -p "$WECHAT_COPY"

# Copy just the executable
cp /Applications/WeChat.app/Contents/MacOS/WeChat "$WECHAT_COPY/WeChat"

# Remove signature and re-sign without hardened runtime and with DYLD env allowed
echo "[*] Re-signing without hardened runtime..."

# Create entitlements that allow DYLD
cat > /tmp/hook_entitlements.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.get-task-allow</key>
    <true/>
</dict>
</plist>
PLIST

codesign --remove-signature "$WECHAT_COPY/WeChat" 2>/dev/null
codesign --force --sign - --entitlements /tmp/hook_entitlements.plist "$WECHAT_COPY/WeChat"
echo "✓ Binary re-signed (ad-hoc, no hardened runtime)"
echo ""

# Step 3: Clear old key file
rm -f "$KEY_FILE"

# Step 4: Launch with hook
# We need to point it to WeChat's frameworks via DYLD_FRAMEWORK_PATH
echo "[*] Launching hooked WeChat..."
echo "    (Wait a few seconds - it will try to open databases and we capture the key)"
echo ""

DYLD_INSERT_LIBRARIES="$HOOK_LIB" \
DYLD_FRAMEWORK_PATH="/Applications/WeChat.app/Contents/Frameworks" \
DYLD_LIBRARY_PATH="/Applications/WeChat.app/Contents/Frameworks" \
"$WECHAT_COPY/WeChat" &>/dev/null &
WECHAT_PID=$!

# Wait for key file
echo -n "[*] Waiting for key capture"
for i in $(seq 1 20); do
    if [ -f "$KEY_FILE" ] && grep -q "key=" "$KEY_FILE" 2>/dev/null; then
        sleep 2
        break
    fi
    sleep 1
    echo -n "."
done
echo ""

# Kill the copy
kill $WECHAT_PID 2>/dev/null
wait $WECHAT_PID 2>/dev/null

# Step 5: Check results
if [ -f "$KEY_FILE" ]; then
    echo ""
    echo "Captured output:"
    cat "$KEY_FILE"
    echo ""
    
    KEY=$(grep "key=" "$KEY_FILE" | grep -oE "[0-9a-f]{64}" | head -1)
    
    if [ -n "$KEY" ] && [ ${#KEY} -eq 64 ]; then
        echo "============================================================"
        echo "  ✅ KEY EXTRACTED: $KEY"
        echo "============================================================"
        echo "$KEY" > "$SCRIPT_DIR/db_key.txt"
        echo ""
        echo "Key saved to: $SCRIPT_DIR/db_key.txt"
        echo ""
        echo "Next steps:"
        echo "  python3 decrypt_and_export.py --key $KEY decrypt"
        echo "  python3 decrypt_and_export.py --key $KEY list"
        echo "  python3 decrypt_and_export.py --key $KEY export --all --format html"
    else
        echo "⚠️  Hook loaded but no 64-char hex key captured."
        echo "    WeChat may use a different function name in version 4.1.11."
        echo "    Check /tmp/wechat_db_key.txt for details."
    fi
else
    echo "❌ No key file created. Process may have been killed by macOS."
    echo ""
    echo "Alternative: Try running the binary directly:"
    echo "  DYLD_INSERT_LIBRARIES=$HOOK_LIB $WECHAT_COPY/WeChat"
    echo ""
    echo "If that also gets killed, we need to try the full .app copy approach."
fi

# Cleanup
rm -rf "$WECHAT_COPY"
