# CPython 3.12 for the Vita

`cpython-3.12.16-vita.patch` applies to the `v3.12.16` tag of
[CPython](https://github.com/python/cpython). It started from Grimiku's
[3.11 Vita fork](https://github.com/Grimiku/cpython-vita) and SonicMastr's
earlier port.

```sh
./build.sh            # clones v3.12.16, applies the patch, builds and installs
```

or by hand:

```sh
git clone --depth 1 --branch v3.12.16 https://github.com/python/cpython
cd cpython
git apply ../cpython-3.12.16-vita.patch
./build_vita.sh
./compile_modules_vita.sh
```

This installs `libpython3.12.a` (every extension module linked in), the
headers and `lib/python312.zip` into VitaSDK. It needs a host Python 3.12
(`BUILD_PYTHON`), and OpenSSL and zlib in VitaSDK.

## What the patch does

- `config.site`, `build_vita.sh`: configure presets for `arm-vita-eabi`, a
  static build with the modules Ren'Py needs, and a check that none were
  skipped.
- `compile_modules_vita.sh`: packs the stdlib as optimized bytecode into
  `python312.zip`.
- `Lib/vitapath.py`, `Lib/os.py`: `os.path` for Vita paths (`ux0:/data/...`,
  device prefixes, no symlinks), frozen in so it's there before the zip is
  mounted.
- `Modules/getpath.*`, `Lib/site.py`: Python's home is the app folder, with
  the stdlib in `lib/python312.zip`.
- `Modules/posixmodule.c`, `fileio.c`, `timemodule.c`, `socketmodule.c` and
  others: the calls newlib on the Vita doesn't have.
- `Python/thread_pthread.h`: 1MB thread stacks, timed waits that use the
  clock the Vita's pthread library actually measures against (with
  `CLOCK_MONOTONIC` every `Lock.acquire(timeout)` and the GIL hand-off
  returned at once), and Python's threads kept off core 0, which the
  launcher gives the main thread.
- `sample/`: a small VPK that checks paths, file I/O, the stdlib modules
  Ren'Py uses and deep pickling on a thread, writing the results to
  `ux0:data/python312-test.txt`.
