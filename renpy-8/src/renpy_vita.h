#ifndef RENPY_VITA_H
#define RENPY_VITA_H

typedef struct RPVITA_MemoryInfo {
    /* newlib heap (where Python and SDL allocate from). */
    unsigned int heap_size;
    unsigned int heap_arena;
    unsigned int heap_used;

    /* Free memory the kernel can still hand out as memory blocks. */
    unsigned int free_user;
    unsigned int free_cdram;
    unsigned int free_phycont;
} RPVITA_MemoryInfo;

void RPVITA_exit_process(int res);
void RPVITA_memory_info(RPVITA_MemoryInfo *info);

void RPVITA_heartbeat(void);
int RPVITA_watchdog_start(int timeout, int armed_for, const char *note);

#endif
