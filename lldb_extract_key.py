#!/usr/bin/env python3
"""
WeChat Mac Key Extractor - LLDB Method

This script creates an lldb command file that extracts the database encryption
key from a running WeChat process on macOS.

Usage:
    python3 lldb_extract_key.py

This will:
1. Generate an lldb script
2. Tell you how to run it manually (since lldb needs special permissions)
3. Validate any key you provide against your local databases

For WeChat Mac 4.x (4.1.11+)
"""

import hashlib
import hmac
import os
import sys
import subprocess


KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096
DEFAULT_ITER = 64000
WECHAT_BASE_PATH = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)


def find_test_db():
    """Find a database file to validate the key against."""
    if not os.path.exists(WECHAT_BASE_PATH):
        return None

    for item in os.listdir(WECHAT_BASE_PATH):
        full_path = os.path.join(WECHAT_BASE_PATH, item)
        if os.path.isdir(full_path) and item not in ("all_users", "Backup"):
            # Try contact.db first, then message DBs
            candidates = [
                os.path.join(full_path, "db_storage", "contact", "contact.db"),
                os.path.join(full_path, "db_storage", "message", "message_0.db"),
            ]
            for c in candidates:
                if os.path.exists(c) and os.path.getsize(c) >= DEFAULT_PAGESIZE:
                    return c
    return None


def validate_key(key_hex, db_path):
    """Validate a key against an encrypted database."""
    if len(key_hex) != 64:
        return False

    try:
        password = bytes.fromhex(key_hex)
    except ValueError:
        return False

    with open(db_path, "rb") as f:
        data = f.read(DEFAULT_PAGESIZE)

    if len(data) < DEFAULT_PAGESIZE:
        return False

    salt = data[:16]
    first_page = data[16:DEFAULT_PAGESIZE]

    byte_key = hashlib.pbkdf2_hmac("sha1", password, salt, DEFAULT_ITER, KEY_SIZE)
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
    hash_mac = hmac.new(mac_key, first_page[:-32], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')

    return hash_mac.digest() == first_page[-32:-12]


def generate_lldb_script():
    """Generate an lldb Python script for key extraction."""

    test_db = find_test_db()
    if not test_db:
        print("[-] No WeChat database found. Is WeChat installed?")
        sys.exit(1)

    # Read the salt from the test DB for inline validation
    with open(test_db, "rb") as f:
        db_header = f.read(DEFAULT_PAGESIZE)

    salt_hex = db_header[:16].hex()
    first_page_hex = db_header[16:DEFAULT_PAGESIZE].hex()

    script = f'''#!/usr/bin/env python3
"""
LLDB Key Extraction Script for WeChat Mac 4.x

Run this inside lldb after attaching to WeChat:
    (lldb) command script import /tmp/wechat_key_extract_lldb.py
    (lldb) wechat_find_key
"""

import lldb
import hashlib
import hmac
import struct
import binascii

KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096
DEFAULT_ITER = 64000

# Pre-loaded from your database file: {os.path.basename(test_db)}
SALT = bytes.fromhex("{salt_hex}")
FIRST_PAGE = bytes.fromhex("{first_page_hex}")


def validate_candidate(candidate):
    """Validate a 32-byte candidate key."""
    byte_key = hashlib.pbkdf2_hmac("sha1", candidate, SALT, DEFAULT_ITER, KEY_SIZE)
    mac_salt = bytes([(SALT[i] ^ 58) for i in range(16)])
    mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
    h = hmac.new(mac_key, FIRST_PAGE[:-32], hashlib.sha1)
    h.update(b'\\x01\\x00\\x00\\x00')
    return h.digest() == FIRST_PAGE[-32:-12]


def find_key(debugger, command, result, internal_dict):
    """Search WeChat process memory for the database encryption key."""
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()

    if not process or not process.IsValid():
        result.AppendMessage("Error: No valid process. Attach to WeChat first.")
        return

    result.AppendMessage("Searching for encryption key in WeChat memory...")
    result.AppendMessage(f"Using salt from: {os.path.basename(test_db)}")

    # Get memory regions
    mem_regions = process.GetMemoryRegions()
    region_info = lldb.SBMemoryRegionInfo()

    candidates_checked = 0
    regions_scanned = 0
    total_bytes = 0

    for i in range(mem_regions.GetSize()):
        mem_regions.GetMemoryRegionAtIndex(i, region_info)

        if not region_info.IsReadable() or not region_info.IsWritable():
            continue

        start = region_info.GetRegionBase()
        end = region_info.GetRegionEnd()
        size = end - start

        # Skip tiny or huge regions
        if size < 256 or size > 50 * 1024 * 1024:
            continue

        regions_scanned += 1

        # Read memory in chunks
        chunk_size = min(size, 2 * 1024 * 1024)
        for offset in range(0, size, chunk_size):
            read_size = min(chunk_size, size - offset)
            error = lldb.SBError()
            data = process.ReadMemory(start + offset, read_size, error)

            if error.Fail() or not data:
                continue

            total_bytes += len(data)

            # Scan for 32-byte keys (8-byte aligned)
            for j in range(0, len(data) - KEY_SIZE, 8):
                candidate = data[j:j + KEY_SIZE]

                # Quick filters
                if candidate[:4] == b'\\x00\\x00\\x00\\x00':
                    continue
                if len(set(candidate)) < 10:
                    continue

                candidates_checked += 1

                if candidates_checked % 50000 == 0:
                    result.AppendMessage(f"  Checked {{candidates_checked}} candidates, scanned {{total_bytes // (1024*1024)}} MB...")

                if validate_candidate(candidate):
                    key_hex = candidate.hex()
                    result.AppendMessage("")
                    result.AppendMessage("=" * 60)
                    result.AppendMessage(f"  KEY FOUND: {{key_hex}}")
                    result.AppendMessage(f"  Address: {{hex(start + offset + j)}}")
                    result.AppendMessage("=" * 60)
                    result.AppendMessage("")
                    result.AppendMessage(f"Save this key! Use it with decrypt_and_export.py:")
                    result.AppendMessage(f"  python3 decrypt_and_export.py --key {{key_hex}} decrypt")
                    result.AppendMessage("")

                    # Save to file
                    key_file = os.path.expanduser("~/project/personal/wechat-extract-mac/db_key.txt")
                    with open(key_file, "w") as f:
                        f.write(key_hex)
                    result.AppendMessage(f"Key also saved to: {{key_file}}")
                    return

    result.AppendMessage(f"\\nKey not found. Scanned {{regions_scanned}} regions, {{total_bytes // (1024*1024)}} MB, {{candidates_checked}} candidates.")
    result.AppendMessage("Try:")
    result.AppendMessage("  1. Make sure WeChat is logged in and has opened some chats")
    result.AppendMessage("  2. Restart WeChat and try again immediately")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand('command script add -f {os.path.basename(__file__).replace(".py", "")}.find_key wechat_find_key')
    print("WeChat key extractor loaded. Run: wechat_find_key")


import os
'''

    script_path = "/tmp/wechat_key_extract_lldb.py"
    with open(script_path, "w") as f:
        f.write(script)

    return script_path, test_db


def main():
    print("=" * 60)
    print("  WeChat Mac Key Extractor - LLDB Method")
    print("=" * 60)
    print()

    # Check if WeChat is running
    result = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True)
    if result.returncode != 0:
        print("[-] WeChat is not running. Please start WeChat and log in first.")
        sys.exit(1)

    pid = int(result.stdout.strip().split('\n')[0])
    print(f"[+] WeChat is running (PID: {pid})")

    # Generate the lldb script
    script_path, test_db = generate_lldb_script()
    print(f"[+] Generated lldb script: {script_path}")
    print(f"[+] Validation DB: {os.path.basename(test_db)}")
    print()

    # Option 1: Try automatic extraction
    print("[*] Attempting automatic key extraction...")
    print("[*] This requires Terminal to have debugging permissions.")
    print("[*] Go to: System Preferences → Privacy & Security → Developer Tools")
    print("[*] And enable Terminal (or your IDE).")
    print()

    # Provide manual instructions
    print("=" * 60)
    print("  MANUAL METHOD (if automatic fails):")
    print("=" * 60)
    print()
    print("  Run these commands in Terminal:")
    print()
    print(f"  1. sudo lldb -p {pid}")
    print(f"  2. (lldb) command script import {script_path}")
    print(f"  3. (lldb) wechat_find_key")
    print(f"  4. (lldb) detach")
    print(f"  5. (lldb) quit")
    print()
    print("  The key will be printed and saved to:")
    print("  ~/project/personal/wechat-extract-mac/db_key.txt")
    print()
    print("=" * 60)
    print()

    # Option 2: If user already has a key, validate it
    print("If you already have a key (from previous extraction or other tools),")
    print("you can validate it here:")
    print()

    # Check for existing key file
    key_file = os.path.expanduser("~/project/personal/wechat-extract-mac/db_key.txt")
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            saved_key = f.read().strip()
        if len(saved_key) == 64 and validate_key(saved_key, test_db):
            print(f"[+] ✓ Existing key is VALID: {saved_key}")
            print(f"[+] You can proceed with decryption:")
            print(f"    python3 decrypt_and_export.py --key {saved_key} decrypt")
            return
        elif len(saved_key) == 64:
            print(f"[-] ✗ Existing key is INVALID (database may have changed)")

    # Interactive key input
    while True:
        try:
            key_input = input("\nEnter key to validate (64 hex chars, or 'q' to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if key_input.lower() == 'q':
            break

        if len(key_input) != 64:
            print(f"[-] Key must be 64 characters, got {len(key_input)}")
            continue

        if validate_key(key_input, test_db):
            print(f"[+] ✓ Key is VALID!")
            with open(key_file, 'w') as f:
                f.write(key_input)
            print(f"[+] Key saved to: {key_file}")
            print(f"[+] Proceed with: python3 decrypt_and_export.py --key {key_input} decrypt")
            break
        else:
            print(f"[-] ✗ Key is INVALID")


if __name__ == "__main__":
    main()
