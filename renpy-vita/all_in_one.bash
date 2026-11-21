#!/bin/bash
# Builds the Summertime to Vita runtime: eboot.bin plus the Python side.
#
# Needs (see the CPython 3.12 Vita port's build_vita.sh for the first two):
#   * VitaSDK with CPython 3.12 installed (libpython3.12.a, python312.zip)
#   * SDL2 built with VitaGL, vitaGL, FFmpeg, freetype + harfbuzz, fribidi
#   * host python3.12 with Cython 3, and cmake
#
# Usage: ./all_in_one.bash [path to the Ren'Py 8.5 vita checkout]
#
# COMMON_RPYC_DIR, if set, is a renpy/common folder where this Ren'Py has
# already compiled its common scripts (for example after starting the game
# once on the PC). Those .rpyc files are shipped too, so the Vita doesn't
# compile the common scripts on its first start.
#
# Output: DistributionTool/ (the runtime files) and summertime-to-vita.vpk.
# TITLE_ID, TITLE_NAME and VITA_ICON can be set in the environment.
set -e

cd "$(dirname "$0")"
HERE=$(pwd)

RENPY_DIR=$(realpath "${1:-../renpy-8}")
BUILD_PYTHON=${BUILD_PYTHON:-python3.12}
CYTHON=${CYTHON:-cython}
OUT="$HERE/DistributionTool"
TMP="$HERE/tmp_build"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

# --- Checks --------------------------------------------------------------

[ -n "$VITASDK" ] || fail "VITASDK is not set."
[ -f "$RENPY_DIR/renpy/vitasupport.py" ] || fail "$RENPY_DIR is not the Ren'Py vita branch."

"$BUILD_PYTHON" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' \
    || fail "$BUILD_PYTHON must be Python 3.12 (set BUILD_PYTHON=...)."

"$CYTHON" --version 2>&1 | grep -q "version 3\." \
    || fail "Cython 3 is required (set CYTHON=...)."

STDLIB_ZIP="$VITASDK/arm-vita-eabi/lib/python312.zip"
[ -f "$STDLIB_ZIP" ] || fail "$STDLIB_ZIP not found. Build CPython 3.12 for the Vita first."

command -v vita-pack-vpk > /dev/null || fail "vita-pack-vpk not found; add \$VITASDK/bin to PATH."

# --- 1. Generate the C sources for Ren'Py's compiled modules ----------------

echo "== Generating Ren'Py C sources"

(
    cd "$RENPY_DIR"
    RENPY_STATIC=1 RENPY_VITA=1 RENPY_CYTHON="$CYTHON" \
        RENPY_CFLAGS= RENPY_LDFLAGS= PYTHONUTF8=1 \
        "$BUILD_PYTHON" setup.py generate
)

# Every generated module has to be registered in the launcher, and the other
# way around, or the eboot fails to link or the import fails at runtime.
generated=$(cd "$RENPY_DIR/tmp/gen3-static" && ls *.c | sed 's/\.c$//' | sort)
registered=$(grep -o '{"[^"]*", PyInit_' source/renpy_modules.h | sed 's/{"//; s/", PyInit_//' | sort)

if [ "$generated" != "$registered" ]; then
    echo "Generated modules and source/renpy_modules.h differ:" >&2
    diff <(echo "$generated") <(echo "$registered") >&2 || true
    fail "Update source/renpy_modules.h."
fi

# --- 2. Build eboot.bin ------------------------------------------------------

echo "== Building eboot.bin"

build_eboot() {
    cmake -S . -B build -DRENPY_DIR="$RENPY_DIR" -DTEXT_PADDING="$1"
    cmake --build build -j"$(nproc)" 2>&1 | tee build/build.log
    return "${PIPESTATUS[0]}"
}

mkdir -p build

if ! build_eboot 0; then
    # The data segment sits on the next 64KB boundary after the code. When
    # the code happens to end less than ~5KB before it, vita-elf-create has
    # no room for its import tables; padding the code by 8KB moves past it.
    grep -q "segment 1 overlaps" build/build.log || fail "eboot.bin build failed."
    echo "== Code segment too close to the data segment, rebuilding with padding"
    build_eboot 8192 || fail "eboot.bin build failed."
fi

# --- 3. Python side -----------------------------------------------------------

echo "== Packaging Python files"

rm -rf "$TMP" "$OUT"
mkdir -p "$TMP/zip" "$OUT/lib" "$OUT/renpy"

# Ren'Py's pure-Python modules (the compiled ones are in the eboot).
(
    cd "$RENPY_DIR"
    find renpy -name '*.py' -not -path 'renpy/common/*' -exec cp --parents {} "$TMP/zip" \;
)

# Third-party packages Ren'Py 8.5 imports at startup.
"$BUILD_PYTHON" -m pip install --quiet --no-compile --target "$TMP/vendor" six ecdsa
cp -r "$TMP/vendor/six.py" "$TMP/vendor/ecdsa" "$TMP/zip/"

"$BUILD_PYTHON" -I -OO -m compileall -q -b "$TMP/zip"
find "$TMP/zip" -name '*.py' -delete
find "$TMP/zip" -name '__pycache__' -prune -exec rm -rf {} +

[ -f "$TMP/zip/renpy/vc_version.pyc" ] || fail "renpy/vc_version.py is missing from $RENPY_DIR."

# One zip: the stdlib from the CPython build, plus Ren'Py and its packages.
cp "$STDLIB_ZIP" "$OUT/lib/python312.zip"
"$BUILD_PYTHON" - "$OUT/lib/python312.zip" "$TMP/zip" <<'EOF'
import os, sys, zipfile
out, src = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            z.write(path, os.path.relpath(path, src))
EOF

# Ren'Py's common scripts, plus the Vita button layout. The layout lives
# with the runtime, not the game files, so a new VPK can change it.
cp -r "$RENPY_DIR/renpy/common" "$OUT/renpy/common"
find "$OUT/renpy/common" \( -name '*.rpyc' -o -name '*.rpymc' -o -name '__pycache__' \) -prune -exec rm -rf {} +
cp "$HERE/../tools/game/vita_controls.rpy" "$OUT/renpy/common/00vita_controls.rpy"

# Their compiled versions, when there are matching ones.
if [ -n "$COMMON_RPYC_DIR" ]; then
    (cd "$COMMON_RPYC_DIR" && find . -type f \( -name '*.rpyc' -o -name '*.rpymc' \) -exec cp --parents {} "$OUT/renpy/common" \;)
fi

cp "$RENPY_DIR/renpy.py" "$OUT/renpy.py"
cp build/eboot.bin "$OUT/eboot.bin"

rm -rf "$TMP"

# --- 4. VPK -------------------------------------------------------------------

# The VPK holds the runtime only. The game files (made by
# tools/prepare_vita_assets.py, about 900MB) go to ux0:data/SummertimeSaga/
# once; new builds don't touch them.

echo "== Packing VPK"

TITLE_ID=${TITLE_ID:-SMTV00001}
TITLE_NAME=${TITLE_NAME:-"Summertime Saga"}
VPK="$HERE/summertime-to-vita.vpk"

vita-mksfoex -s TITLE_ID="$TITLE_ID" -d ATTRIBUTE2=12 "$TITLE_NAME" build/param.sfo

pack_args=()

while IFS= read -r -d '' f; do
    rel=${f#"$OUT/"}
    [ "$rel" = "eboot.bin" ] && continue
    pack_args+=(-a "$f=$rel")
done < <(find "$OUT" -type f -print0)

# Optional LiveArea icon: a 128x128 8-bit PNG.
if [ -n "$VITA_ICON" ]; then
    pack_args+=(-a "$VITA_ICON=sce_sys/icon0.png")
fi

rm -f "$VPK"
vita-pack-vpk -s build/param.sfo -b "$OUT/eboot.bin" "${pack_args[@]}" "$VPK"

echo
echo "Done:"
echo "  $VPK"
echo "Install it. The game files go to ux0:data/SummertimeSaga/ (only needed once)."
