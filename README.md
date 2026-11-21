# Summertime to Vita

A PlayStation Vita port of the Ren'Py runtime that runs **Summertime Saga
0.20.16**. It's Ren'Py 8.5 on CPython 3.12, drawing with vitaGL, built as a
native Vita application.

This repository only has the engine side: the launcher, the Vita build of
Ren'Py, the CPython patch and the tools that turn the game's Android release
into something the Vita can load. **The game itself is not included.** You
need your own copy of the Summertime Saga 0.20.16 Android release to build the
game files.

## What's in here

| Folder | What it is |
|---|---|
| `renpy-vita/` | The Vita application: `main.c`, the CMake project and `all_in_one.bash`, which builds `eboot.bin` and the VPK. |
| `renpy-8/` | Ren'Py 8.5.3 with the Vita changes (memory, loading, input, rendering, Python 2 compatibility for the game's scripts). Engine sources only. |
| `cpython/` | A patch for CPython 3.12.16 that makes it build and run on the Vita, and a script that applies it and installs Python into VitaSDK. |
| `tools/` | Turns the game's APK into the Vita game folder, crops and repacks its art, converts its Python 2 snippets, and holds the Vita button layout. |

## How it fits together

1. CPython 3.12 is cross-compiled for the Vita and installed into VitaSDK
   (`libpython3.12.a`, headers, `python312.zip`).
2. Ren'Py's Cython modules are turned into C and compiled straight into
   `eboot.bin`, together with Python, SDL2, vitaGL, FFmpeg, FreeType and the
   rest. The Vita can't load shared libraries, so everything is static.
3. The VPK only carries the runtime (about 18MB). The game's files go to
   `ux0:data/SummertimeSaga/`, so a new build installs in seconds.

See [BUILDING.md](BUILDING.md) for the details.

## Installing a build

1. Put `libshacccg.suprx` in `ur0:data/` (vitaGL compiles shaders with it;
   see the [extraction guide](https://cimmerian.gitbook.io/vita-troubleshooting-guide/shader-compiler/extract-libshacccg.suprx)).
2. Copy the prepared game folder to `ux0:data/SummertimeSaga/`.
3. Install `summertime-to-vita.vpk` with VitaShell.

The first start builds Ren'Py's caches and compiles shaders, so it's slower
than the next ones.

## Controls

Touch works as on Android. The left stick moves a pointer and Cross clicks
under it; the D-pad moves between buttons. The full layout is in
[BUILDING.md](BUILDING.md#controls).

## Status

The game runs from start to finish of what has been tested, but performance
is still being worked on: loading art and laying out text are the main costs
on the Vita's CPU. `log.txt` and `profile.txt` in the app folder report frame
rate, memory and where the time goes, and are the most useful thing to attach
to a bug report.

## Credits

Ported by Akurey. This builds on a lot of other people's work; see
[CREDITS.md](CREDITS.md).

Summertime Saga is made by Kompas Productions. This project isn't affiliated
with them.

## License

The launcher and tools are MIT licensed ([LICENSE](LICENSE)). Ren'Py and
CPython keep their own licenses; see `renpy-8/LICENSE.txt` and
[CREDITS.md](CREDITS.md).
