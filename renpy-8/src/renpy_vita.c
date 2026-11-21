/* Copyright 2026 Akurey
 *
 * PlayStation Vita helpers for Ren'Py (Summertime to Vita). MIT license,
 * same terms as the rest of Ren'Py.
 */

#include <string.h>
#include <malloc.h>

#include <psp2/kernel/processmgr.h>
#include <psp2/kernel/sysmem.h>
#include <psp2/kernel/threadmgr.h>
#include <stdio.h>

#include "renpy_vita.h"

/* Set by the launcher (main.c). Weak so the module still links if an
   embedder doesn't define it; heap_size is reported as 0 then. */
extern int _newlib_heap_size_user __attribute__((weak));

void RPVITA_exit_process(int res) {
    sceKernelExitProcess(res);
}

void RPVITA_memory_info(RPVITA_MemoryInfo *out) {
    struct mallinfo mi = mallinfo();
    SceKernelFreeMemorySizeInfo free_info;

    memset(out, 0, sizeof(*out));

    if (&_newlib_heap_size_user != NULL) {
        out->heap_size = (unsigned int) _newlib_heap_size_user;
    }

    out->heap_arena = (unsigned int) mi.arena;
    out->heap_used = (unsigned int) mi.uordblks;

    memset(&free_info, 0, sizeof(free_info));
    free_info.size = sizeof(free_info);

    if (sceKernelGetFreeMemorySize(&free_info) >= 0) {
        out->free_user = (unsigned int) free_info.size_user;
        out->free_cdram = (unsigned int) free_info.size_cdram;
        out->free_phycont = (unsigned int) free_info.size_phycont;
    }
}

/* Freeze watchdog. The game bumps the heartbeat once per drawn frame; if it
   stops moving for `timeout` seconds while the watchdog is armed, the
   watchdog leaves a note and crashes on purpose, so the system writes a core
   dump with every thread's registers and stack. Only seconds the watchdog
   thread actually runs are counted, so time spent suspended doesn't count. */

static volatile unsigned int heartbeat;
static volatile int watchdog_seconds_left;
static int watchdog_timeout;
static char watchdog_note[256];

void RPVITA_heartbeat(void) {
    heartbeat++;
}

static int watchdog_thread(SceSize args, void *argp) {
    unsigned int last = heartbeat;
    int stalled = 0;

    while (watchdog_seconds_left > 0) {
        sceKernelDelayThread(1000 * 1000);
        watchdog_seconds_left--;

        if (heartbeat != last) {
            last = heartbeat;
            stalled = 0;
            continue;
        }

        if (++stalled < watchdog_timeout) {
            continue;
        }

        FILE *f = fopen(watchdog_note, "w");

        if (f) {
            fprintf(f, "No frame drawn for %d seconds (%u frames before that).\n"
                "Crashing on purpose so the system writes a core dump.\n",
                stalled, last);
            fclose(f);
        }

        __builtin_trap();
    }

    return sceKernelExitDeleteThread(0);
}

int RPVITA_watchdog_start(int timeout, int armed_for, const char *note) {
    SceUID thid;

    if (watchdog_seconds_left > 0) {
        return 0;
    }

    watchdog_timeout = timeout;
    watchdog_seconds_left = armed_for;
    strncpy(watchdog_note, note, sizeof(watchdog_note) - 1);

    thid = sceKernelCreateThread("rpvita_watchdog", watchdog_thread, 0x10000100, 0x4000, 0, 0, NULL);

    if (thid < 0) {
        watchdog_seconds_left = 0;
        return thid;
    }

    return sceKernelStartThread(thid, 0, NULL);
}
