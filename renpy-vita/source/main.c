/*
 * Summertime to Vita - Ren'Py 8.5 launcher for the PlayStation Vita.
 *
 * Based on the RenPy-Vita launchers by SonicMastr and Grimiku. Updated for
 * Ren'Py 8.5 / Python 3.12 by Akurey.
 *
 * Starts Python with the stdlib from lib/python312.zip, registers Ren'Py's
 * compiled modules and runs renpy.py from the app directory.
 */

#include <errno.h>
#include <reent.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <Python.h>
#include <SDL2/SDL.h>

#include <psp2/appmgr.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/io/stat.h>
#include <psp2/kernel/cpu.h>
#include <psp2/kernel/modulemgr.h>
#include <psp2/kernel/sysmem.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/power.h>
#include <psp2/vshbridge.h>

#include "renpy_modules.h"

/* Memory layout. Python, SDL and the game state live in the newlib heap;
   VitaGL's pools are allocated outside it, from whatever is left (it keeps
   24MB of that free). The heap is sized at boot, below. */
int _newlib_heap_size_user = 190 * 1024 * 1024;
unsigned int sceLibcHeapSize = 10 * 1024 * 1024;

/* The newlib heap, replacing the one in VitaSDK's libc (sbrk.c), which takes
   a fixed size and can't start at all if that much isn't free. This one
   looks at what's free at boot: in the extended memory mode (ATTRIBUTE2=12)
   that's about 100MB more than the standard 256MB partition, and the game
   needs it far more than VitaGL does. HEAP_RESERVE stays free for VitaGL's
   pools, SceLibc's heap and thread stacks. The heap is never smaller than
   the size the game was tuned with in the standard mode, and if the memory
   block can't be had it steps down until it can. */
#define HEAP_MIN (190u << 20)
#define HEAP_MAX (300u << 20)
#define HEAP_RESERVE (96u << 20)
#define HEAP_FLOOR (64u << 20)

static SceUID heap_block = -1;
static char *heap_base;
static char *heap_cur;
static char *heap_end;
static SceKernelLwMutexWork heap_mutex __attribute__((aligned(8)));

void _init_vita_heap(void) {
    SceKernelFreeMemorySizeInfo info;
    unsigned int size = HEAP_MIN;
    void *base = NULL;

    sceKernelCreateLwMutex(&heap_mutex, "Newlib heap", 0, 0, NULL);

    memset(&info, 0, sizeof(info));
    info.size = sizeof(info);

    if (sceKernelGetFreeMemorySize(&info) >= 0 && (unsigned int) info.size_user > HEAP_RESERVE) {
        unsigned int want = ((unsigned int) info.size_user - HEAP_RESERVE) & ~0xFFFFFu;

        if (want > size)
            size = want;
        if (size > HEAP_MAX)
            size = HEAP_MAX;
    }

    for (;;) {
        heap_block = sceKernelAllocMemBlock("Newlib heap", SCE_KERNEL_MEMBLOCK_TYPE_USER_RW, size, NULL);

        if (heap_block >= 0 || size <= HEAP_FLOOR)
            break;

        size -= 8u << 20;
    }

    if (heap_block < 0 || sceKernelGetMemBlockBase(heap_block, &base) < 0) {
        heap_base = heap_cur = heap_end = NULL;
        return;
    }

    heap_base = heap_cur = (char *) base;
    heap_end = heap_base + size;
    _newlib_heap_size_user = (int) size;
}

void _free_vita_heap(void) {
    sceKernelDeleteLwMutex(&heap_mutex);

    if (heap_block >= 0)
        sceKernelFreeMemBlock(heap_block);

    heap_block = -1;
    heap_base = heap_cur = heap_end = NULL;
}

int _get_vita_heap_size(void) {
    return heap_base ? (int) (heap_end - heap_base) : 0;
}

void *_sbrk_r(struct _reent *reent, ptrdiff_t incr) {
    char *prev;

    if (sceKernelLockLwMutex(&heap_mutex, 1, NULL) < 0) {
        reent->_errno = ENOMEM;
        return (void *) -1;
    }

    prev = heap_cur;

    if (heap_base == NULL || (incr > 0 && incr > heap_end - heap_cur) || (incr < 0 && -incr > heap_cur - heap_base)) {
        sceKernelUnlockLwMutex(&heap_mutex, 1);
        reent->_errno = ENOMEM;
        return (void *) -1;
    }

    heap_cur += incr;
    sceKernelUnlockLwMutex(&heap_mutex, 1);
    return prev;
}

/* Python runs on the main thread. 2MB covers the interpreter's C recursion
   limit (1000 on the Vita) when pickling deep save data. */
unsigned int sceUserMainThreadStackSize = 2 * 1024 * 1024;

static char app_dir[0x100];
static char renpy_py[0x100];
static char program_path[0x100];

/* vita-elf-create appends ~5KB of import tables to the end of the code
   segment, and fails when the data segment (aligned to 64KB) starts closer
   than that. all_in_one.bash rebuilds with TEXT_PADDING set when that
   happens, which moves the end of the code segment out of the way. */
#if defined(TEXT_PADDING) && TEXT_PADDING > 0
__attribute__((used)) static const char text_padding[TEXT_PADDING] = { 1 };
#endif

/* Logs sit next to the game files, where they're easy to fetch over FTP. */
#define LOG_DIR "ux0:data/SummertimeSaga"

#define SHADER_CACHE_DIR LOG_DIR "/shader_cache"

/* VitaGL's disk cache for compiled shaders. */
void vglSetShaderCachePath(const char *path);
#define NATIVE_LOG LOG_DIR "/boot-native.log"

/* Runs renpy.py with Python's output going to LOG_DIR/boot.log. On the
   Vita stdout and stderr go nowhere, so without this an exception during
   startup just closes the app. renpy.py runs in __main__ itself, since
   Ren'Py looks its helpers up there (renpy.__main__). */
static const char *boot_script =
    "import sys, os, time, traceback\n"
    "def _open_log():\n"
    "    for d in ('" LOG_DIR "', 'ux0:data'):\n"
    "        try:\n"
    "            return open(d + '/boot.log', 'w', buffering=1, encoding='utf-8', errors='replace')\n"
    "        except OSError:\n"
    "            pass\n"
    "    return None\n"
    "_log = _open_log()\n"
    "if _log is not None:\n"
    "    sys.stdout = sys.stderr = _log\n"
    "print('Summertime Saga (Vita) boot', time.strftime('%Y-%m-%d %H:%M:%S'))\n"
    "print('Python', sys.version)\n"
    "print('argv', sys.argv, 'path', sys.path)\n"
    "def _run(path):\n"
    "    import __main__\n"
    "    g = __main__.__dict__\n"
    "    g['__file__'] = path\n"
    "    with open(path, 'rb') as f:\n"
    "        code = compile(f.read(), path, 'exec')\n"
    "    try:\n"
    "        exec(code, g)\n"
    "    except SystemExit as e:\n"
    "        print('SystemExit', repr(e.code))\n"
    "    except BaseException:\n"
    "        print('Startup failed:')\n"
    "        traceback.print_exc()\n"
    "    finally:\n"
    "        print('renpy.py returned', time.strftime('%H:%M:%S'))\n"
    "        sys.stdout.flush()\n";

static void fatal(const char *message) {
    printf("%s\n", message);
    fflush(stdout);
    sceKernelExitProcess(1);
}

static int check_status(PyStatus status, PyConfig *config) {
    if (!PyStatus_Exception(status)) {
        return 0;
    }

    PyConfig_Clear(config);

    if (PyStatus_IsExit(status)) {
        sceKernelExitProcess(status.exitcode);
    }

    Py_ExitStatusException(status);
    return 1;
}

static void init_python(void) {
    PyStatus status;
    PyConfig config;

    if (PyImport_ExtendInittab(renpy_modules) < 0) {
        fatal("Could not register Ren'Py modules.");
    }

    PyConfig_InitPythonConfig(&config);

    config.site_import = 0;
    config.user_site_directory = 0;
    config.optimization_level = 2;
    config.write_bytecode = 0;
    config.install_signal_handlers = 0;

    status = PyConfig_SetBytesString(&config, &config.home, app_dir);
    check_status(status, &config);

    status = PyConfig_SetBytesString(&config, &config.program_name, program_path);
    check_status(status, &config);

    /* renpy.py takes the Ren'Py base directory from argv[0]. */
    char *argv[] = { app_dir };
    status = PyConfig_SetBytesArgv(&config, 1, argv);
    check_status(status, &config);

    config.parse_argv = 0;

    status = Py_InitializeFromConfig(&config);
    check_status(status, &config);

    PyConfig_Clear(&config);
}

int main(int argc, char *argv[]) {
    char title_id[0x10];

    (void) argc;
    (void) argv;

    scePowerSetArmClockFrequency(444);
    scePowerSetBusClockFrequency(222);
    scePowerSetGpuClockFrequency(222);
    scePowerSetGpuXbarClockFrequency(166);

    /* The game runs on this thread; give it core 0 to itself. Python's other
       threads keep to cores 1 and 2 (Python/thread_pthread.h). */
    sceKernelChangeThreadCpuAffinityMask(sceKernelGetThreadId(), SCE_KERNEL_CPU_MASK_USER_0);

    /* Compiled shaders are kept on the memory card, so each one is compiled
       (which takes seconds) only the first time it's ever used. */
    sceIoMkdir(SHADER_CACHE_DIR, 0777);
    sceIoMkdir(SHADER_CACHE_DIR "/v", 0777);
    sceIoMkdir(SHADER_CACHE_DIR "/f", 0777);
    vglSetShaderCachePath(SHADER_CACHE_DIR);

    /* Ren'Py writes into its own directory (cache, logs), which needs the
       app directory mounted read-write instead of app0:. */
    sceAppMgrUmount("app0:");

    memset(title_id, 0, sizeof(title_id));
    sceAppMgrAppParamGetString(sceKernelGetProcessId(), 12, title_id, sizeof(title_id) - 1);

    snprintf(app_dir, sizeof(app_dir), "ux0:app/%s", title_id);
    snprintf(program_path, sizeof(program_path), "%s/eboot.bin", app_dir);
    snprintf(renpy_py, sizeof(renpy_py), "%s/renpy.py", app_dir);

    /* Native messages (this file, SDL, Python's own startup errors). */
    freopen(NATIVE_LOG, "w", stdout);
    freopen(NATIVE_LOG, "a", stderr);
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    printf("launcher: app %s\n", app_dir);

    /* Only the front touch screen acts as the mouse; the rear pad stays off
       so holding the Vita doesn't tap things. */
#ifdef SDL_HINT_VITA_TOUCH_MOUSE_DEVICE
    SDL_SetHint(SDL_HINT_VITA_TOUCH_MOUSE_DEVICE, "0");
#endif

    /* The PS TV outputs 720p by default. */
    if (vshSblAimgrIsDolce()) {
        SDL_setenv("VITA_RESOLUTION", "720", 1);
    }

    sceKernelLoadStartModule("vs0:sys/external/libfios2.suprx", 0, NULL, 0, NULL, NULL);
    sceKernelLoadStartModule("vs0:sys/external/libc.suprx", 0, NULL, 0, NULL, NULL);

    init_python();
    printf("launcher: python started\n");

    if (PyRun_SimpleString(boot_script) != 0) {
        fatal("launcher: boot script failed");
    }

    PyObject *main_module = PyImport_AddModule("__main__");
    PyObject *run = main_module ? PyObject_GetAttrString(main_module, "_run") : NULL;
    PyObject *result = run ? PyObject_CallFunction(run, "s", renpy_py) : NULL;

    if (result == NULL) {
        PyErr_Print();
    }

    Py_XDECREF(result);
    Py_XDECREF(run);

    printf("launcher: exiting\n");
    fflush(stdout);

    Py_Finalize();
    sceKernelExitProcess(0);
    return 0;
}
