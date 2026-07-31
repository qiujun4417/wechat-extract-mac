#!/usr/bin/env python3
"""
WeChat Mac Database Key Extractor

Extracts the SQLCipher encryption key from a running WeChat process on macOS.
Works with WeChat Mac 4.x (tested on 4.1.11).

The approach:
1. Attach to the WeChat process via lldb
2. Search memory for the 32-byte raw key
3. Validate the candidate key against an encrypted database file

Requires:
- WeChat to be running and logged in
- lldb (comes with Xcode Command Line Tools)
- May need to disable SIP or grant debugging permissions

Alternative method: If lldb approach fails, we try scanning /proc-style memory
via macOS Mach APIs (requires root).
"""

import hashlib
import hmac
import os
import re
import subprocess
import sys
import tempfile
import struct

# Constants matching WeChat's SQLCipher configuration
KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096
DEFAULT_ITER = 64000
SQLITE_FILE_HEADER = b"SQLite format 3\x00"


def get_wechat_pid():
    """Get WeChat process PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "WeChat"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split('\n')[0])
    except Exception:
        pass
    return None


def find_wechat_db_path():
    """Find the WeChat database directory on macOS."""
    base_path = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    if not os.path.exists(base_path):
        return None

    # Find user directories (format: username_hash)
    user_dirs = []
    for item in os.listdir(base_path):
        full_path = os.path.join(base_path, item)
        if os.path.isdir(full_path) and item not in ("all_users", "Backup"):
            db_storage = os.path.join(full_path, "db_storage")
            if os.path.exists(db_storage):
                user_dirs.append(full_path)

    return user_dirs


def get_db_salt(db_path):
    """Read the first 16 bytes (salt) from an encrypted database."""
    with open(db_path, "rb") as f:
        salt = f.read(16)
    return salt


def validate_key(key_bytes, db_path):
    """
    Validate a candidate key against an encrypted WeChat database.
    Uses the same PBKDF2/HMAC-SHA1 verification as SQLCipher.
    """
    if len(key_bytes) != KEY_SIZE:
        return False

    try:
        with open(db_path, "rb") as f:
            data = f.read(DEFAULT_PAGESIZE)
    except (IOError, OSError):
        return False

    if len(data) < DEFAULT_PAGESIZE:
        return False

    salt = data[:16]
    first_page = data[16:DEFAULT_PAGESIZE]

    # Derive the encryption key
    byte_key = hashlib.pbkdf2_hmac("sha1", key_bytes, salt, DEFAULT_ITER, KEY_SIZE)

    # Derive the HMAC key
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)

    # Compute HMAC
    hash_mac = hmac.new(mac_key, first_page[:-32], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')

    return hash_mac.digest() == first_page[-32:-12]


def extract_key_via_lldb(pid, db_path):
    """
    Extract the key by using lldb to search WeChat process memory.

    This works by:
    1. Attaching lldb to the WeChat process
    2. Searching memory for potential 32-byte keys
    3. Validating each candidate against the DB file
    """
    print(f"[*] Attempting to extract key via lldb (PID: {pid})...")
    print("[*] This may require granting terminal debugging permissions.")
    print("[*] If prompted, enter your password or approve in System Preferences.")

    # Create an lldb script that dumps heap memory regions
    lldb_script = f"""
process attach --pid {pid}
script
import lldb
target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()

# Get all memory regions
regions = []
region_info = lldb.SBMemoryRegionInfo()
mem_regions = process.GetMemoryRegions()

for i in range(mem_regions.GetSize()):
    mem_regions.GetMemoryRegionAtIndex(i, region_info)
    if region_info.IsReadable() and region_info.IsWritable():
        start = region_info.GetRegionBase()
        end = region_info.GetRegionEnd()
        size = end - start
        # Only scan reasonably sized writable regions (likely heap)
        if 4096 <= size <= 100 * 1024 * 1024:
            regions.append((start, size))

print(f"SCAN_REGIONS_COUNT:{{len(regions)}}")

# Write addresses to a file for the outer script to process
with open("/tmp/wechat_mem_regions.txt", "w") as f:
    for start, size in regions:
        f.write(f"{{start}},{{size}}\\n")

# Read memory and look for potential keys
# The key is 32 bytes, typically aligned
import hashlib, hmac, struct

KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096
DEFAULT_ITER = 64000

db_path = "{db_path}"
with open(db_path, "rb") as f:
    db_data = f.read(DEFAULT_PAGESIZE)

salt = db_data[:16]
first_page = db_data[16:DEFAULT_PAGESIZE]

found_key = None
scanned = 0
for start, size in regions:
    if found_key:
        break
    # Read in chunks
    chunk_size = min(size, 4 * 1024 * 1024)  # 4MB chunks
    for offset in range(0, size, chunk_size):
        read_size = min(chunk_size, size - offset)
        error = lldb.SBError()
        data = process.ReadMemory(start + offset, read_size, error)
        if error.Fail() or not data:
            continue
        scanned += len(data)
        # Scan for potential 32-byte keys (aligned to 8 bytes)
        for i in range(0, len(data) - KEY_SIZE, 8):
            candidate = data[i:i+KEY_SIZE]
            # Quick filter: skip if all zeros or all same byte
            if candidate == b'\\x00' * KEY_SIZE:
                continue
            if len(set(candidate)) < 8:
                continue
            # Validate
            byte_key = hashlib.pbkdf2_hmac("sha1", candidate, salt, DEFAULT_ITER, KEY_SIZE)
            mac_salt = bytes([(salt[j] ^ 58) for j in range(16)])
            mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
            h = hmac.new(mac_key, first_page[:-32], hashlib.sha1)
            h.update(b'\\x01\\x00\\x00\\x00')
            if h.digest() == first_page[-32:-12]:
                found_key = candidate.hex()
                print(f"KEY_FOUND:{{found_key}}")
                break
        if found_key:
            break

if not found_key:
    print(f"KEY_NOT_FOUND:scanned_{{scanned}}_bytes")

process.Detach()
quit
"""

    # Write lldb script to temp file
    script_path = "/tmp/wechat_lldb_extract.py"
    with open(script_path, "w") as f:
        f.write(lldb_script)

    print("[*] Running lldb memory scan (this may take a few minutes)...")
    try:
        result = subprocess.run(
            ["lldb", "--batch", "--source-on-crash", "quit", "--one-line", f"command source {script_path}"],
            input=lldb_script,
            capture_output=True, text=True,
            timeout=600
        )
        output = result.stdout + result.stderr
        # Look for our key marker
        for line in output.split('\n'):
            if 'KEY_FOUND:' in line:
                key_hex = line.split('KEY_FOUND:')[1].strip()
                return key_hex
    except subprocess.TimeoutExpired:
        print("[-] lldb timed out")
    except Exception as e:
        print(f"[-] lldb error: {e}")

    return None


def extract_key_via_memory_scan(pid, db_path):
    """
    Alternative: use a C helper or direct memory access to scan for the key.
    This compiles and runs a small helper program.
    """
    print(f"[*] Attempting direct memory scan (PID: {pid})...")

    # Create a C program that uses mach_vm_read to scan process memory
    c_code = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <pid>\n", argv[0]);
        return 1;
    }

    pid_t pid = atoi(argv[1]);
    task_t task;
    kern_return_t kr;

    kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "task_for_pid failed: %s (try running with sudo)\n", mach_error_string(kr));
        return 1;
    }

    // Enumerate memory regions and dump readable ones
    mach_vm_address_t address = 0;
    mach_vm_size_t size = 0;
    natural_t depth = 0;
    vm_region_submap_info_data_64_t info;
    mach_msg_type_number_t count;

    FILE *out = fopen("/tmp/wechat_memory_dump.bin", "wb");
    FILE *meta = fopen("/tmp/wechat_memory_meta.txt", "w");
    if (!out || !meta) {
        fprintf(stderr, "Cannot open output files\n");
        return 1;
    }

    long total_written = 0;
    int region_count = 0;

    while (1) {
        count = VM_REGION_SUBMAP_INFO_COUNT_64;
        kr = mach_vm_region_recurse(task, &address, &size, &depth,
                                     (vm_region_recurse_info_t)&info, &count);
        if (kr != KERN_SUCCESS) break;

        if (info.is_submap) {
            depth++;
            continue;
        }

        // Only scan readable/writable regions (heap, data)
        if ((info.protection & VM_PROT_READ) && (info.protection & VM_PROT_WRITE)) {
            if (size <= 50 * 1024 * 1024) { // Skip huge regions
                mach_vm_size_t read_size = 0;
                vm_offset_t data = 0;
                mach_msg_type_number_t data_count = 0;

                // Read in chunks
                mach_vm_size_t chunk = (size < 4*1024*1024) ? size : 4*1024*1024;
                for (mach_vm_size_t off = 0; off < size; off += chunk) {
                    mach_vm_size_t to_read = (size - off < chunk) ? (size - off) : chunk;
                    kr = mach_vm_read(task, address + off, to_read, &data, &data_count);
                    if (kr == KERN_SUCCESS && data_count > 0) {
                        fwrite((void*)data, 1, data_count, out);
                        total_written += data_count;
                        mach_vm_deallocate(mach_task_self(), data, data_count);
                    }
                }
                fprintf(meta, "%llu,%llu\n", (unsigned long long)address, (unsigned long long)size);
                region_count++;
            }
        }

        address += size;
    }

    fclose(out);
    fclose(meta);

    printf("DUMP_COMPLETE:%ld bytes from %d regions\n", total_written, region_count);
    return 0;
}
"""
    # Write, compile, and run
    c_path = "/tmp/wechat_memscan.c"
    bin_path = "/tmp/wechat_memscan"

    with open(c_path, "w") as f:
        f.write(c_code)

    # Compile
    result = subprocess.run(
        ["clang", "-o", bin_path, c_path, "-framework", "IOKit"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[-] Compilation failed: {result.stderr}")
        return None

    # Run (needs root for task_for_pid)
    print("[*] Running memory scanner (may need sudo)...")
    result = subprocess.run(
        ["sudo", bin_path, str(pid)],
        capture_output=True, text=True,
        timeout=120
    )

    if "DUMP_COMPLETE" not in result.stdout:
        print(f"[-] Memory dump failed: {result.stdout} {result.stderr}")
        return None

    print(f"[+] {result.stdout.strip()}")

    # Now scan the dump for the key
    return scan_dump_for_key("/tmp/wechat_memory_dump.bin", db_path)


def scan_dump_for_key(dump_path, db_path):
    """Scan a memory dump file for the SQLCipher key."""
    print("[*] Scanning memory dump for encryption key...")

    with open(db_path, "rb") as f:
        db_data = f.read(DEFAULT_PAGESIZE)

    salt = db_data[:16]
    first_page = db_data[16:DEFAULT_PAGESIZE]

    dump_size = os.path.getsize(dump_path)
    print(f"[*] Dump size: {dump_size / 1024 / 1024:.1f} MB")

    found_key = None
    checked = 0

    with open(dump_path, "rb") as f:
        # Read in chunks for memory efficiency
        chunk_size = 8 * 1024 * 1024  # 8MB
        overlap = KEY_SIZE  # Overlap to catch keys at chunk boundaries
        prev_tail = b""

        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            data = prev_tail + chunk
            prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk

            # Scan every 8 bytes (alignment)
            for i in range(0, len(data) - KEY_SIZE, 8):
                candidate = data[i:i + KEY_SIZE]

                # Quick filters
                if candidate[:4] == b'\x00\x00\x00\x00':
                    continue
                if len(set(candidate)) < 10:
                    continue

                checked += 1
                if checked % 100000 == 0:
                    print(f"[*] Checked {checked} candidates...", end='\r')

                # Full validation
                byte_key = hashlib.pbkdf2_hmac("sha1", candidate, salt, DEFAULT_ITER, KEY_SIZE)
                mac_salt = bytes([(salt[j] ^ 58) for j in range(16)])
                mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
                h = hmac.new(mac_key, first_page[:-32], hashlib.sha1)
                h.update(b'\x01\x00\x00\x00')

                if h.digest() == first_page[-32:-12]:
                    found_key = candidate.hex()
                    print(f"\n[+] KEY FOUND: {found_key}")
                    return found_key

    print(f"\n[-] Key not found after checking {checked} candidates")
    return None


def extract_key_via_lldb_breakpoint(pid, db_path):
    """
    Use lldb to set a breakpoint on sqlite3_key_v2 or similar function
    and capture the key argument when it's called.

    This requires restarting WeChat or triggering a DB open.
    """
    print("[*] This method requires WeChat to open a database.")
    print("[*] You may need to restart WeChat after attaching lldb.")
    # This is more complex and intrusive - implement as last resort
    return None


def save_key(key_hex, user_dir):
    """Save the extracted key to a file."""
    key_file = os.path.join(user_dir, "db_key.txt")
    with open(key_file, "w") as f:
        f.write(key_hex)
    print(f"[+] Key saved to: {key_file}")
    return key_file


def main():
    print("=" * 60)
    print("  WeChat Mac Database Key Extractor")
    print("  For WeChat Mac 4.x (SQLCipher encrypted databases)")
    print("=" * 60)
    print()

    # Step 1: Find WeChat process
    pid = get_wechat_pid()
    if not pid:
        print("[-] WeChat is not running. Please start WeChat and log in first.")
        sys.exit(1)
    print(f"[+] Found WeChat process (PID: {pid})")

    # Step 2: Find database files
    user_dirs = find_wechat_db_path()
    if not user_dirs:
        print("[-] No WeChat data directory found.")
        sys.exit(1)

    print(f"[+] Found {len(user_dirs)} WeChat user(s):")
    for i, d in enumerate(user_dirs):
        print(f"    [{i}] {os.path.basename(d)}")

    # Use the first user (or let user choose)
    user_dir = user_dirs[0]
    db_storage = os.path.join(user_dir, "db_storage")

    # Find a database file to validate against
    msg_dir = os.path.join(db_storage, "message")
    contact_dir = os.path.join(db_storage, "contact")

    test_db = None
    for candidate in [
        os.path.join(contact_dir, "contact.db"),
        os.path.join(msg_dir, "message_0.db"),
    ]:
        if os.path.exists(candidate) and os.path.getsize(candidate) >= DEFAULT_PAGESIZE:
            test_db = candidate
            break

    if not test_db:
        print("[-] No suitable database file found for key validation.")
        sys.exit(1)

    print(f"[+] Using database for validation: {os.path.basename(test_db)}")
    print()

    # Step 3: Try to extract the key
    key_hex = None

    # Check if key was previously extracted
    key_file = os.path.join(user_dir, "db_key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r") as f:
            saved_key = f.read().strip()
        if len(saved_key) == 64:
            print(f"[*] Found previously saved key, validating...")
            key_bytes = bytes.fromhex(saved_key)
            if validate_key(key_bytes, test_db):
                print(f"[+] Saved key is valid!")
                key_hex = saved_key
            else:
                print(f"[-] Saved key is no longer valid.")

    if not key_hex:
        # Method 1: Direct memory scan (needs root)
        print("[*] Method 1: Direct memory scan (requires sudo)")
        key_hex = extract_key_via_memory_scan(pid, test_db)

    if not key_hex:
        # Method 2: lldb approach
        print()
        print("[*] Method 2: lldb memory scan")
        key_hex = extract_key_via_lldb(pid, test_db)

    if key_hex:
        print()
        print("=" * 60)
        print(f"  SUCCESS! Database encryption key:")
        print(f"  {key_hex}")
        print("=" * 60)
        save_key(key_hex, user_dir)
        return key_hex
    else:
        print()
        print("=" * 60)
        print("  FAILED to extract key automatically.")
        print()
        print("  Manual alternatives:")
        print("  1. Disable SIP and retry with sudo")
        print("  2. Use the lldb manual method (see README)")
        print("  3. If you know the key, save it to:")
        print(f"     {key_file}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
