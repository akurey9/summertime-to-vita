# Building Summertime to Vita

Three pieces get built, in this order:

1. **CPython 3.12** for the Vita, installed into VitaSDK.
2. **The runtime**: `eboot.bin` with Ren'Py compiled in, packed as a VPK.
3. **The game files**: Summertime Saga 0.20.16, taken from its Android
   release and packed for the Vita.

Everything runs on Linux (WSL2 works; WSL1 doesn't run VitaSDK well).

## Requirements

On the host:

- [VitaSDK](https://vitasdk.org), with `$VITASDK` set and `$VITASDK/bin` on `PATH`
- Python 3.12 with `pip install "cython>=3.0,<3.2" setuptools pillow`
  (point `BUILD_PYTHON` and `CYTHON` at it if it's a virtualenv)
- `cmake`, `git`

In VitaSDK (most come from `vdpm`):

- vitaGL, vitaShaRK and math-neon, and SDL2 built with the vitaGL backend
- SDL2_image
- FFmpeg (avformat, avcodec, avutil, swresample, swscale, avfilter), LAME, mpg123
- freetype built with harfbuzz, fribidi
- libpng, libjpeg, libwebp, zlib, bzip2, OpenSSL 1.1.1

On the Vita:

- `ur0:data/libshacccg.suprx` (vitaGL compiles Ren'Py's shaders at runtime
  with it; see the
  [extraction guide](https://cimmerian.gitbook.io/vita-troubleshooting-guide/shader-compiler/extract-libshacccg.suprx))

## 1. CPython 3.12

```sh
cd cpython
./build.sh
```

This clones CPython 3.12.16, applies `cpython-3.12.16-vita.patch` and
installs `libpython3.12.a`, the headers and `lib/python312.zip` into VitaSDK.
`build_vita.sh` stops if a module Ren'Py needs wasn't built. See
[cpython/README.md](cpython/README.md).

Optional check on hardware: the patch adds `sample/`, a small VPK that tests
paths, file I/O, the stdlib modules Ren'Py uses and deep pickling on a
thread. Its results are written to `ux0:data/python312-test.txt`.

```sh
cd cpython/cpython-src/sample && cmake -B build && cmake --build build
```

## 2. The runtime

```sh
cd renpy-vita
./all_in_one.bash ../renpy-8
```

Optionally, set `COMMON_RPYC_DIR` to a `renpy/common` folder where this
Ren'Py has already compiled its common scripts (start the game once on the
PC with the same tree). Those `.rpyc` files are shipped too, so the Vita
doesn't compile them on its first start.

The script:

1. generates C for Ren'Py's Cython modules (`RENPY_STATIC=1 RENPY_VITA=1
   setup.py generate`),
2. checks that every generated module is registered in
   `source/renpy_modules.h`,
3. builds `eboot.bin`,
4. packs Ren'Py's Python files and the stdlib into `lib/python312.zip`,
5. builds `summertime-to-vita.vpk`.

`TITLE_ID` (default `SMTV00001`), `TITLE_NAME` (default "Summertime
Saga", the LiveArea bubble name) and `VITA_ICON` (a 128x128 8-bit PNG for
LiveArea) can be set in the environment.

CMake links `libpython3.12.a` by name and doesn't notice when it changes, so
after rebuilding CPython delete `renpy-vita/build/eboot.elf` to force a
relink.

If Ren'Py's module list changes, regenerate `source/renpy_modules.h` from
the `.c` files in `renpy-8/tmp/gen3-static/`; the script says which ones
differ.

## 3. The game files

The game is Summertime Saga 0.20.16, from its Android release
(`SummertimeSaga-0.20.16-build2-release.apk`). Its scripts were compiled by
Ren'Py 7 for Python 2; `renpy/compat/legacy.py` runs them on this Ren'Py:

- `py3_fixes.json` holds the scripts' Python converted by 2to3. Make it by
  starting the game once on the PC with `RENPY_DUMP_PYCODE=dump.jsonl` set
  (every snippet is written there as it's compiled), then:

  ```sh
  python3 tools/make_py3_fixes.py dump.jsonl py3_fixes.json
  ```

  This needs a Python that still has lib2to3 (3.12 or older).
- Python 2 behaviour 2to3 can't add is kept at compile time: `int / int`
  rounds down, `__nonzero__` and `__div__` keep working, and classes that
  define `__eq__` stay hashable.

The whole script doesn't fit in the Vita's memory, so story is loaded when
it's needed. Script files that only hold story are skipped at startup;
files that also have init code are loaded for it, and their story is
dropped until the game jumps there. The index of those files is made on the
PC by starting the game once with `RENPY_LAZY_INDEX=vita_lazy_scripts.json`
set; `RENPY_LAZY_INIT_DIR=vita_init` makes the same run write an init-only
copy of each file that has init code, so the Vita loads just that part at
startup. Adding `RENPY_COMPILE_ALL=1` to that run writes a complete
`cache/bytecode-312.rpyb` too, which is the cache to ship.

Then build the folder:

```sh
python3 tools/prepare_vita_assets.py SummertimeSaga-0.20.16-build2-release.apk \
    out/SummertimeSaga --py3-fixes py3_fixes.json \
    --lazy-index vita_lazy_scripts.json --init-dir vita_init \
    --cache /path/to/game/cache
```

and convert the art:

```sh
python3 tools/crop_vita_images.py out/SummertimeSaga/images.rpa out/converted
rm out/SummertimeSaga/images.rpa
mv out/converted/images*.rpa out/SummertimeSaga/
python3 -c "import sys; sys.path.insert(0, 'tools'); \
    from prepare_vita_assets import write_file_list; write_file_list('out/SummertimeSaga')"
```

The Android release stores its art as lossy WebP, which is slow to decode on
the Vita, and every character layer is a mostly empty 1024x768 canvas.
`crop_vita_images.py` crops each image to its visible area, stores it as PNG
and records where it goes in `vita_crops.json`; the engine puts it back in
place. The output is split into archives under 2GB (`images.rpa`,
`images2.rpa`), since the Vita's C library uses 32-bit file offsets.

`prepare_vita_assets.py` also copies the compiled Python and screen caches
from `--cache` (the `cache/` folder of a PC run with the same engine), and
`vita_files.txt` lets the engine check at startup that every archive was
copied completely.

## Installing

1. Copy the contents of `out/SummertimeSaga/` to `ux0:data/SummertimeSaga/`
   (FTP or USB), so that `ux0:data/SummertimeSaga/images.rpa` exists. Only
   needed once per game release.
2. Install `summertime-to-vita.vpk` with VitaShell. For a new build, just
   install the new VPK; the game files stay where they are.
3. Start the game. The first start builds Ren'Py's caches and compiles
   shaders (kept in `ux0:data/SummertimeSaga/shader_cache`), so it takes
   longer than the next ones.

The engine looks for the game in `ux0:data/SummertimeSaga`;
`RENPY_VITA_GAMEDIR` overrides that.

Saves go to `ux0:/Ren'Py Data/summertimesaga/`. If the game closes during
startup, `ux0:data/SummertimeSaga/boot.log` has Python's output and the
error.

## Controls

Touch works as on the Android release. On top of that:

| Button | Action |
|---|---|
| Cross | Confirm / advance (clicks under the pointer while it shows) |
| Circle | Closes the phone or backpack; the phone while walking around; otherwise the pause menu |
| Triangle | Town map while walking around; otherwise hides the text box |
| Square | Quick menu on / off |
| Select | Backpack while walking around |
| Start | Pause menu |
| L / R | Rollback / skip |
| D-pad | Moves between buttons and hotspots |
| Left stick | Pointer |
| Hold Select, press Start | Debug overlay |

## Memory

The game runs in the extended memory mode (`ATTRIBUTE2=12`). `main.c`
sizes the newlib heap at boot from what's free (between 190 and 300MB) and
leaves the rest to vitaGL. The main thread, which runs the game, is pinned
to core 0; Python's other threads (image preloading, audio upkeep) run on
cores 1 and 2.

`renpy/vitasupport.py` keeps the heap in check: above 80% it runs a garbage
collection, above 90% it drops caches and layered images and does a full
collection. The limits are at the top of the file
(`RENPY_VITA_HEAP_SOFT_LIMIT` and friends can also be set with `SDL_setenv()`
in `source/main.c` before Python starts).

## Diagnostics

Written next to the app (`ux0:app/SMTV00001/`):

- `log.txt`: Ren'Py's log, with a line every 10 seconds giving frame rate,
  draw time, image decodes and uploads, heap and free memory, and what's on
  screen. Images that take over 150ms to load are listed by name.
- `profile.txt`: a sampling profile of the main thread, rewritten every
  minute. Creating `noprofile.txt` in the same folder turns it off.
- `freeze.txt`: the main thread's stack whenever no frame was drawn for 8
  seconds.

Hold Select and press Start for the on-screen overlay (frame rate, heap,
free RAM and CDRAM, image cache).
