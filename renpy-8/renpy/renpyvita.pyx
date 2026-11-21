# Bindings for src/renpy_vita.c. Only built for the PlayStation Vita.

cdef extern from "renpy_vita.h":

    ctypedef struct RPVITA_MemoryInfo:
        unsigned int heap_size
        unsigned int heap_arena
        unsigned int heap_used
        unsigned int free_user
        unsigned int free_cdram
        unsigned int free_phycont

    void RPVITA_exit_process(int res)
    void RPVITA_memory_info(RPVITA_MemoryInfo *info)
    void RPVITA_heartbeat()
    int RPVITA_watchdog_start(int timeout, int armed_for, const char *note)


def exit_process(int res):
    RPVITA_exit_process(res)


def memory_info():
    """
    Returns a dict of memory statistics, all in bytes:

    `heap_size`, `heap_arena`, `heap_used`
        The newlib heap: its fixed size, how much of it malloc has claimed,
        and how much is in use.

    `free_user`, `free_cdram`, `free_phycont`
        What the kernel can still allocate outside the heap (VitaGL's
        texture pools come from here).
    """

    cdef RPVITA_MemoryInfo info

    RPVITA_memory_info(&info)

    return {
        "heap_size" : info.heap_size,
        "heap_arena" : info.heap_arena,
        "heap_used" : info.heap_used,
        "free_user" : info.free_user,
        "free_cdram" : info.free_cdram,
        "free_phycont" : info.free_phycont,
    }


def heartbeat():
    RPVITA_heartbeat()


def watchdog_start(int timeout, int armed_for, note):
    """
    Starts the native freeze watchdog. See renpy_vita.c.
    """

    cdef bytes b = note.encode("utf-8")
    return RPVITA_watchdog_start(timeout, armed_for, b)
