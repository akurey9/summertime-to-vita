#!/bin/sh
# Builds CPython 3.12.16 for the Vita and installs it into VitaSDK.
#
# Usage: ./build.sh [work directory]   (default: ./cpython-src)
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$HERE/cpython-src}
TAG=v3.12.16

if [ -z "$VITASDK" ]; then
    echo "VITASDK is not set." >&2
    exit 1
fi

if [ ! -d "$SRC" ]; then
    git clone --depth 1 --branch "$TAG" https://github.com/python/cpython "$SRC"
    (cd "$SRC" && git apply "$HERE/cpython-3.12.16-vita.patch")
fi

cd "$SRC"
sh ./build_vita.sh
sh ./compile_modules_vita.sh
