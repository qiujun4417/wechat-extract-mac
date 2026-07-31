#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <CommonCrypto/CommonKeyDerivation.h>
#include <CommonCrypto/CommonHMAC.h>

#define KEY_SIZE 32
#define DEFAULT_PAGESIZE 4096
#define DEFAULT_ITER 64000

static unsigned char g_salt[16];
static unsigned char g_first_page[4080]; // 4096 - 16

int validate_key(const unsigned char *candidate) {
    unsigned char byte_key[KEY_SIZE];
    unsigned char mac_salt[16];
    unsigned char mac_key[KEY_SIZE];
    unsigned char hmac_result[20];
    unsigned int hmac_len = 20;
    
    // Derive encryption key
    CCKeyDerivationPBKDF(kCCPBKDF2, (const char*)candidate, KEY_SIZE,
                         g_salt, 16, kCCPRFHmacAlgSHA1,
                         DEFAULT_ITER, byte_key, KEY_SIZE);
    
    // Derive HMAC key
    for (int i = 0; i < 16; i++) mac_salt[i] = g_salt[i] ^ 58;
    CCKeyDerivationPBKDF(kCCPBKDF2, (const char*)byte_key, KEY_SIZE,
                         mac_salt, 16, kCCPRFHmacAlgSHA1,
                         2, mac_key, KEY_SIZE);
    
    // Compute HMAC
    // HMAC(mac_key, first_page[:-32] + b'\x01\x00\x00\x00')
    CCHmacContext ctx;
    CCHmacInit(&ctx, kCCHmacAlgSHA1, mac_key, KEY_SIZE);
    CCHmacUpdate(&ctx, g_first_page, 4080 - 32); // first_page[:-32]
    unsigned char suffix[] = {0x01, 0x00, 0x00, 0x00};
    CCHmacUpdate(&ctx, suffix, 4);
    CCHmacFinal(&ctx, hmac_result);
    
    // Compare: hmac_result should equal first_page[-32:-12]
    return memcmp(hmac_result, g_first_page + 4080 - 32, 20) == 0;
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <pid> <db_path>\n", argv[0]);
        return 1;
    }
    
    pid_t pid = atoi(argv[1]);
    const char *db_path = argv[2];
    
    // Read DB header for validation
    FILE *db = fopen(db_path, "rb");
    if (!db) {
        fprintf(stderr, "Cannot open database: %s\n", db_path);
        return 1;
    }
    fread(g_salt, 1, 16, db);
    fread(g_first_page, 1, 4080, db);
    fclose(db);
    
    fprintf(stderr, "Salt: ");
    for (int i = 0; i < 16; i++) fprintf(stderr, "%02x", g_salt[i]);
    fprintf(stderr, "\n");
    
    // Get task port
    task_t task;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "task_for_pid failed: %s\n", mach_error_string(kr));
        fprintf(stderr, "Try running with: sudo %s %s %s\n", argv[0], argv[1], argv[2]);
        return 1;
    }
    
    fprintf(stderr, "Attached to PID %d, scanning memory...\n", pid);
    
    // Scan memory regions
    mach_vm_address_t address = 0;
    mach_vm_size_t size = 0;
    natural_t depth = 0;
    vm_region_submap_info_data_64_t info;
    mach_msg_type_number_t count;
    
    long total_scanned = 0;
    long candidates_checked = 0;
    int regions_scanned = 0;
    
    while (1) {
        count = VM_REGION_SUBMAP_INFO_COUNT_64;
        kr = mach_vm_region_recurse(task, &address, &size, &depth,
                                     (vm_region_recurse_info_t)&info, &count);
        if (kr != KERN_SUCCESS) break;
        
        if (info.is_submap) {
            depth++;
            continue;
        }
        
        // Only scan readable+writable regions (heap)
        if ((info.protection & VM_PROT_READ) && (info.protection & VM_PROT_WRITE)) {
            if (size >= 256 && size <= 50 * 1024 * 1024) {
                regions_scanned++;
                
                // Read in chunks
                mach_vm_size_t chunk_size = (size < 4*1024*1024) ? size : 4*1024*1024;
                
                for (mach_vm_size_t off = 0; off < size; off += chunk_size) {
                    mach_vm_size_t to_read = (size - off < chunk_size) ? (size - off) : chunk_size;
                    vm_offset_t data = 0;
                    mach_msg_type_number_t data_count = 0;
                    
                    kr = mach_vm_read(task, address + off, to_read, &data, &data_count);
                    if (kr != KERN_SUCCESS || data_count == 0) continue;
                    
                    unsigned char *buf = (unsigned char*)data;
                    total_scanned += data_count;
                    
                    // Scan for 32-byte keys (8-byte aligned)
                    for (mach_vm_size_t j = 0; j + KEY_SIZE <= data_count; j += 8) {
                        // Quick filter: skip all zeros
                        if (*(unsigned int*)(buf + j) == 0) continue;
                        
                        // Check uniqueness of bytes
                        unsigned char seen[256] = {0};
                        int unique = 0;
                        for (int k = 0; k < KEY_SIZE; k++) {
                            if (!seen[buf[j+k]]) { seen[buf[j+k]] = 1; unique++; }
                        }
                        if (unique < 10) continue;
                        
                        candidates_checked++;
                        
                        if (candidates_checked % 100000 == 0) {
                            fprintf(stderr, "\r  Scanned %ld MB, checked %ld candidates, %d regions...",
                                    total_scanned / (1024*1024), candidates_checked, regions_scanned);
                        }
                        
                        if (validate_key(buf + j)) {
                            fprintf(stderr, "\n\nKEY FOUND!\n");
                            // Print key to stdout
                            for (int k = 0; k < KEY_SIZE; k++) printf("%02x", buf[j+k]);
                            printf("\n");
                            
                            fprintf(stderr, "Address: 0x%llx\n", (unsigned long long)(address + off + j));
                            mach_vm_deallocate(mach_task_self(), data, data_count);
                            return 0;
                        }
                    }
                    
                    mach_vm_deallocate(mach_task_self(), data, data_count);
                }
            }
        }
        
        address += size;
    }
    
    fprintf(stderr, "\n\nKey not found. Scanned %ld MB, %ld candidates in %d regions.\n",
            total_scanned / (1024*1024), candidates_checked, regions_scanned);
    return 1;
}
