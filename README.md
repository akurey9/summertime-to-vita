[2026-11-21-112949-025575.bmp](https://github.com/user-attachments/files/35213584/2026-11-21-112949-025575.bmp)

# Summertime to Vita

A custom PlayStation Vita port of the Ren'Py runtime made to run **Summertime Saga
0.20.16**. It's Ren'Py 8.5 on CPython 3.12, drawing with vitaGL, built as a
native PS Vita application. (BASED ON SONICMASTR'S REN'PY VITA AND GRIMIKU'S REN'PY 8 VITA)

## What's in here

| Folder | |
|---|---|
| `renpy-vita/` | The Vita application: `main.c`, the CMake project and `all_in_one.bash`, which builds `eboot.bin` and the VPK. |
| `renpy-8/` | Ren'Py 8.5.3 compiled for the Vita (memory, loading, input, rendering, Python 2 compatibility for the game's scripts). |
| `cpython/` | A patch for CPython 3.12.16 that makes it build and run on the Vita, and a script that applies it and installs Python into VitaSDK. |
| `tools/` | Turns the game's APK into the Vita game folder, crops and repacks its art, converts its Python 2 snippets, and holds the Vita button layout. |

See [BUILDING.md](BUILDING.md) for the details.

## Controls

Touch works as on Android. The left stick moves a pointer and Cross clicks
under it; the D-pad moves between buttons. The full layout is in
[BUILDING.md](BUILDING.md#controls).

## Status

The game runs from "start to finish" of what has been tested, but performance
is still being worked on: loading art and laying out text are the main costs
on the Vita's CPU. `log.txt` and `profile.txt` in the app folder report frame
rate, memory and where the time goes, and are the most useful thing to attach
to a bug report.

## Credits

Ported by Akurey. This builds on a lot of other people's work; see
[CREDITS.md](CREDITS.md).

SonicMastr's Ren'py Vita: https://github.com/SonicMastr/renpy-vita
Grimiku's Ren'py 8 Vita: https://github.com/Grimiku/RenPy-Vita-8

Summertime Saga is made by Kompas Productions. This project isn't affiliated
with them.

## License

The launcher and tools are MIT licensed ([LICENSE](LICENSE)). Ren'Py and
CPython keep their own licenses; see `renpy-8/LICENSE.txt` and
[CREDITS.md](CREDITS.md).
