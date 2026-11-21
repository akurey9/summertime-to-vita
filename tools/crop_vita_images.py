#!/usr/bin/env python3
# Summertime to Vita - art conversion for faster loading. MIT license.

"""
Rewrites images.rpa so the Vita can load its art quickly.

The Android release stores nearly all of its art as lossy WebP (under .png
names), and each character layer is a full 1024x768 canvas that is mostly
transparent. Decoding that on the Vita's CPU takes around a tenth of a
second per image, and a line of dialogue can need dozens.

Each WebP/PNG image is decoded, cropped to its visible area (plus a pixel of
transparent margin, so its edges filter cleanly), and stored as PNG.
vita_crops.json, inside the archive, records for every cropped file the size
of the full image and where the stored part goes in it; Ren'Py on the Vita
(renpy/display/im.py) puts it back in place. JPEG art and other files are
copied unchanged.

The Vita's C library uses 32-bit file offsets, so no archive may reach 2GB.
The output is split into OUT_DIR/images.rpa, images2.rpa, ... each under
MAX_ARCHIVE bytes; Ren'Py loads every .rpa in the game folder.

Usage:

    python3 crop_vita_images.py SummertimeSaga/images.rpa OUT_DIR [--jobs N]
    python3 crop_vita_images.py --split ARCHIVE.rpa OUT_DIR

--split only re-splits an archive this tool already converted. Afterwards,
copy the images*.rpa files over the game folder's (removing the old ones)
and run prepare_vita_assets.py's write_file_list on it.
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import os
import pickle
import sys
import time
import zlib

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prepare_vita_assets import RPAWriter, write_file_list  # noqa: E402

CROPS_NAME = "vita_crops.json"

# Below 2GB, with room for the index.
MAX_ARCHIVE = 1900 * 1024 * 1024


class SplitWriter:
    """
    Writes images.rpa, images2.rpa, ... in out_dir, starting a new archive
    before one would reach MAX_ARCHIVE.
    """

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.paths = []
        self.writer = None
        self.size = 0
        self.next()

    def next(self):
        if self.writer is not None:
            self.writer.close()

        n = len(self.paths) + 1
        path = os.path.join(self.out_dir, "images.rpa" if n == 1 else "images%d.rpa" % n)
        self.paths.append(path)
        self.writer = RPAWriter(path)
        self.size = 0

    def add(self, name, data):
        if self.size and self.size + len(data) > MAX_ARCHIVE:
            self.next()

        self.writer.add(name, data)
        self.size += len(data)

    def close(self):
        self.writer.close()

        for path in self.paths:
            assert os.path.getsize(path) < 2 ** 31, path


def read_index(path):
    with open(path, "rb") as f:
        header = f.read(40).split(b"\n")[0].split()
        offset = int(header[1], 16)
        key = int(header[2], 16)
        f.seek(offset)
        index = pickle.loads(zlib.decompress(f.read()))

    rv = []

    for name, parts in index.items():
        start, length = parts[0][0] ^ key, parts[0][1] ^ key
        rv.append((name, start, length))

    rv.sort(key=lambda i: i[1])
    return rv


def convert(job):
    """
    Returns (name, data, crop), where crop is [width, height, x, y] or None.
    """

    name, data = job

    if not name.lower().endswith(".png") or not (data[:4] == b"RIFF" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return name, data, None

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return name, data, None

    if im.mode != "RGBA":
        im = im.convert("RGBA")

    width, height = im.size
    bbox = im.getchannel("A").getbbox()

    if bbox is None:
        bbox = (0, 0, 1, 1)

    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - 1)
    y0 = max(0, y0 - 1)
    x1 = min(width, x1 + 1)
    y1 = min(height, y1 + 1)

    crop = None

    if (x0, y0, x1, y1) != (0, 0, width, height):
        im = im.crop((x0, y0, x1, y1))
        crop = [width, height, x0, y0]

    out = io.BytesIO()
    im.save(out, "PNG", compress_level=6)

    return name, out.getvalue(), crop


def jobs(src, entries):
    with open(src, "rb") as f:
        for name, start, length in entries:
            f.seek(start)
            yield name, f.read(length)


def split(src, out_dir):
    writer = SplitWriter(out_dir)

    for name, data in jobs(src, read_index(src)):
        writer.add(name, data)

    writer.close()

    for path in writer.paths:
        print("%s %d bytes" % (path, os.path.getsize(path)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out_dir")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--split", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    if args.split:
        split(args.src, args.out_dir)
        return

    entries = read_index(args.src)
    entries = [ e for e in entries if e[0] != CROPS_NAME ]

    writer = SplitWriter(args.out_dir)
    crops = {}
    before = after = 0
    start = time.time()

    with multiprocessing.Pool(args.jobs) as pool:
        for n, (name, data, crop) in enumerate(pool.imap(convert, jobs(args.src, entries), chunksize=16)):
            writer.add(name, data)
            after += len(data)

            if crop is not None:
                crops[name] = crop

            if n % 1000 == 0:
                print("%d/%d, %.0f s" % (n, len(entries), time.time() - start), flush=True)

    for _name, _start, length in entries:
        before += length

    writer.add(CROPS_NAME, json.dumps(crops, separators=(",", ":")).encode("utf-8"))
    writer.close()

    print("%d files, %d cropped; %.0f MB -> %.0f MB in %.0f s, %d archives" % (
        len(entries), len(crops), before / 1048576.0, after / 1048576.0, time.time() - start,
        len(writer.paths)))


if __name__ == "__main__":
    main()
