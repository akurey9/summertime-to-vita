#!/usr/bin/env python3
# Copyright 2026 Akurey
#
# Summertime to Vita - asset preparation. MIT license.

"""
Builds the Vita's game folder for Summertime Saga 0.20.16 from the Android
release (the .apk).

The Android release keeps the game in the APK's assets/ folder, with "x-"
in front of every path component, as loose files: 1024x768 art, audio,
fonts and the compiled scripts. The art is drawn as it is, since 1024x768
is close to the Vita's screen. This tool packs it into four archives, so
there are a handful of files to copy instead of twenty thousand, and adds:

- py3_fixes.json, the scripts' Python converted for Python 3 (made by
  make_py3_fixes.py), inside scripts.rpa;
- vita_lazy_scripts.json, the index of script files the Vita loads only
  when the story needs them (made by starting the game on the PC with
  RENPY_LAZY_INDEX set), inside scripts.rpa, with the init-only copies of
  script files that same run writes to RENPY_LAZY_INIT_DIR (vita_init/);
- cache/, the compiled Python and screen analysis from a run on the PC, so
  the Vita doesn't spend its first start compiling;
- vita_files.txt, the size and index checksum of each archive, which the
  Vita checks at startup to catch incomplete copies.

Usage:

    python3 prepare_vita_assets.py GAME.apk OUT_DIR --py3-fixes py3_fixes.json         --lazy-index vita_lazy_scripts.json --init-dir vita_init [--cache DIR]

On the Vita, OUT_DIR's contents go to ux0:data/SummertimeSaga/.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pickle
import shutil
import zipfile
import zlib

RPA_HEADER_SIZE = 34  # b"RPA-3.0 " + 16 hex + b" " + 8 hex + b"\n"
RPA_KEY = 0x42424242

# Top-level folder in the game -> archive it goes into.
ARCHIVES = {
    "images" : "images.rpa",
    "audio" : "audio.rpa",
    "fonts" : "fonts.rpa",
    "scripts" : "scripts.rpa",
    }

# Loose files kept next to the archives.
COPY_FILES = ("changelog.txt", "pledge_list.txt", "presplash.jpg", "script_version.txt")

# Caches made by a run on the PC. The Android release's own cache/ holds
# Python 2 bytecode and prerendered images this Ren'Py doesn't use.
CACHE_FILES = ("bytecode-312.rpyb", "py3analysis.rpyb", "screens.rpyb")


class RPAWriter:
    """
    Writes a RPA-3.0 archive in the same layout Ren'Py's archiver uses.
    """

    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(b"\0" * RPA_HEADER_SIZE)
        self.f.write(b"Made with Ren'Py.")
        self.index = {}

    def add(self, name, data):
        offset = self.f.tell()
        self.f.write(data)
        self.index[name] = [(offset ^ RPA_KEY, len(data) ^ RPA_KEY, b"")]

    def close(self):
        index_offset = self.f.tell()
        self.f.write(zlib.compress(pickle.dumps(self.index, 2), 9))
        self.f.seek(0)
        self.f.write(b"RPA-3.0 %016x %08x\n" % (index_offset, RPA_KEY))
        self.f.close()


def game_files(apk):
    """
    Yields (name, ZipInfo) for each file of the game in the APK, with the
    "x-" prefixes removed. Names are relative to the game folder.
    """

    for info in apk.infolist():
        if info.is_dir() or not info.filename.startswith("assets/x-game/"):
            continue

        parts = info.filename[len("assets/"):].split("/")
        parts = [ p[2:] if p.startswith("x-") else p for p in parts ]

        yield "/".join(parts[1:]), info


def write_file_list(out):
    lines = []

    for name in sorted(os.listdir(out)):
        if not name.lower().endswith(".rpa"):
            continue

        path = os.path.join(out, name)

        with open(path, "rb") as f:
            header = f.read(40)
            f.seek(int(header[8:24], 16))
            sha1 = hashlib.sha1(f.read()).hexdigest()

        lines.append("%s %d %s" % (name, os.path.getsize(path), sha1))

    with open(os.path.join(out, "vita_files.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print("vita_files.txt (%d archives)" % len(lines))


def main():
    ap = argparse.ArgumentParser(description="Build the Vita game folder for Summertime Saga 0.20.16.")
    ap.add_argument("apk", help="the Android release (.apk)")
    ap.add_argument("out", help="output folder (created; its old archives are replaced)")
    ap.add_argument("--py3-fixes", required=True, help="py3_fixes.json from make_py3_fixes.py")
    ap.add_argument("--lazy-index", required=True, help="vita_lazy_scripts.json from a PC run with RENPY_LAZY_INDEX")
    ap.add_argument("--init-dir", required=True, help="init-only script copies from the RENPY_LAZY_INIT_DIR run")
    ap.add_argument("--cache", help="cache/ folder of a PC run with the same engine")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    writers = {}
    counts = {}

    with zipfile.ZipFile(args.apk) as apk:

        if "assets/x-game/x-script_version.txt" not in apk.namelist():
            raise SystemExit(f"{args.apk} doesn't look like the game's Android release.")

        for name, info in game_files(apk):
            top = name.split("/", 1)[0]

            if top in ARCHIVES:
                archive = ARCHIVES[top]

                if archive not in writers:
                    print(archive)
                    writers[archive] = RPAWriter(os.path.join(args.out, archive))
                    counts[archive] = 0

                writers[archive].add(name, apk.read(info))
                counts[archive] += 1

            elif name in COPY_FILES:
                with apk.open(info) as src, open(os.path.join(args.out, name), "wb") as dst:
                    shutil.copyfileobj(src, dst)

    with open(args.py3_fixes, "rb") as f:
        writers["scripts.rpa"].add("py3_fixes.json", f.read())

    with open(args.lazy_index, "rb") as f:
        writers["scripts.rpa"].add("vita_lazy_scripts.json", f.read())

    count = 0

    for dirpath, _dirnames, filenames in os.walk(args.init_dir):
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            name = "vita_init/" + os.path.relpath(path, args.init_dir).replace(os.sep, "/")

            with open(path, "rb") as f:
                writers["scripts.rpa"].add(name, f.read())

            count += 1

    print("  vita_init: %d init-only script files" % count)

    for archive, w in writers.items():
        w.close()
        print("  %s: %d files, %d MB" % (archive, counts[archive], os.path.getsize(os.path.join(args.out, archive)) // 2**20))

    if args.cache:
        dst = os.path.join(args.out, "cache")
        os.makedirs(dst, exist_ok=True)

        for name in CACHE_FILES:
            src = os.path.join(args.cache, name)

            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(dst, name))
                print("cache/" + name)

    write_file_list(args.out)

    print("Done:", args.out)


if __name__ == "__main__":
    main()
