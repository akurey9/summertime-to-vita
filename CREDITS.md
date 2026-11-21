# Credits

Summertime to Vita is put together and maintained by **Akurey**. It stands on
the work below.

## Earlier Vita ports

- **SonicMastr** (Jaylon Gowie): [renpy-vita](https://github.com/SonicMastr/renpy-vita),
  the original Ren'Py 7 port for the Vita, and the first Python port it was
  built on.
- **Grimiku** (Mikołaj Kupiec): [RenPy-Vita-8](https://github.com/Grimiku/RenPy-Vita-8),
  the Ren'Py 8 / Python 3.11 update with vitaGL support, and the
  [CPython Vita fork](https://github.com/Grimiku/cpython-vita) the 3.12 patch
  started from.
- **Julian Uy**: work on the earlier launchers (see LICENSE).

The launcher in `renpy-vita/` descends from these projects and keeps their
MIT license.

## Engine and runtime

- [Ren'Py](https://www.renpy.org) by Tom Rothamel and contributors (MIT).
  It includes code from pygame and pygame_sdl2 (LGPL / zlib),
  libhydrogen by Frank Denis (ISC) and others, as noted in the source files.
- [CPython](https://www.python.org) by the Python Software Foundation and
  contributors (PSF License).
- [Cython](https://cython.org), used at build time to turn Ren'Py's modules
  into C.

## Vita platform

- [VitaSDK](https://vitasdk.org): the toolchain, headers and system library
  stubs.
- [vitaGL](https://github.com/Rinnegatamante/vitaGL),
  [vitaShaRK](https://github.com/Rinnegatamante/vitaShaRK) and
  [math-neon](https://github.com/Rinnegatamante/math-neon) by Rinnegatamante.
- [SDL2](https://www.libsdl.org) and its Vita port, with the vitaGL backend
  from [Northfear's fork](https://github.com/Northfear/SDL); SDL2_image.
- pthread-embedded, as packaged by VitaSDK.
- `libshacccg.suprx` is Sony's shader compiler. It isn't distributed here;
  users extract it from their own console.

## Libraries linked into the eboot

FFmpeg (libavformat, libavcodec, libavfilter, libavutil, libswresample,
libswscale), LAME, mpg123, FreeType, HarfBuzz, FriBidi, libpng,
libjpeg-turbo, libwebp, zlib, bzip2 and OpenSSL, all as packaged for VitaSDK
(mostly through vdpm). Each keeps its own license.

## The game

Summertime Saga is made by Kompas Productions. None of the game's files are
in this repository.
