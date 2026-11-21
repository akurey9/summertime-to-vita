# Ren'Py 8.5.3 for the Vita

This is [Ren'Py](https://github.com/renpy/renpy) 8.5.3, trimmed to the engine
(`renpy/`, `src/`, `setup.py`, `renpy.py`) and changed to run on the
PlayStation Vita. The launcher, SDK, docs and tutorial aren't needed and
aren't here.

`renpy-vita/all_in_one.bash` builds it: `RENPY_STATIC=1 RENPY_VITA=1
setup.py generate` turns the Cython modules into C, which is compiled into
`eboot.bin`; the Python files go into `lib/python312.zip`.

## What's changed

Everything Vita-only is behind `renpy.vita` (Python) or `__vita__` (C), so the
tree still behaves as stock Ren'Py elsewhere.

- `renpy/vitasupport.py`: memory policy, the left stick pointer and
  controller layout, the game-file check at startup, the debug overlay,
  `log.txt` performance lines and the sampling profiler.
- `src/renpy_vita.c`, `renpy/renpyvita.pyx`: memory statistics, process
  exit and a freeze watchdog, from C.
- `renpy/compat/legacy.py`: runs scripts compiled by Ren'Py 7 for Python 2
  (2to3 conversions loaded from `py3_fixes.json`, classic division, old
  class behaviour, Python 2 built-ins).
- `renpy/script.py`, `renpy/loader.py`: story files are loaded when the game
  first needs them; only init code is loaded at startup. Loose files are
  looked up in an index instead of stat'ing the memory card.
- `renpy/display/im.py`, `renpy/display/pgrender.py`: art stored cropped to
  its visible area (see `tools/crop_vita_images.py`), images read in one go,
  recolouring done on the GPU, small surfaces kept for image manipulators.
- `renpy/common/00layeredimage_ren.py`: layered images are built the first
  time they're shown, with cached attribute lookups.
- `renpy/gl2/`: texture upload and render-to-texture paths for vitaGL, the
  shader list kept between sessions.
- `renpy/text/`: FreeType text instead of HarfBuzz shaping, shared text
  layouts, larger font caches.
- `renpy/savelocation.py`: no background polling of the save folder.
- `renpy/display/core.py`, `behavior.py`, `controller.py`: touch and the
  on-screen keyboard.
- `src/renpysound_core.c`: the mixing buffers live on the heap, since the
  audio thread's stack is small. `src/ffmedia.c` doesn't use
  `posix_memalign`, which newlib lacks.
