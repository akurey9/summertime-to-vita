#!/usr/bin/env python3
# Builds py3_fixes.json for a game whose scripts were compiled by Ren'Py 7.
#
# 1. Start the game once on the PC with this Ren'Py and RENPY_DUMP_PYCODE set
#    to a file name; every Python snippet in the scripts is written there as
#    it's compiled.
# 2. Run this with a Python that still has lib2to3 (3.12 or older):
#
#        python3 make_py3_fixes.py dump.jsonl game/py3_fixes.json
#
# The output maps each snippet that 2to3 changes to its converted text.
# renpy/compat/legacy.py loads it and compiles the converted text instead.

import collections
import json
import sys
import warnings

warnings.simplefilter("ignore")

from lib2to3.refactor import RefactoringTool, get_fixers_from_package

# fix_import turns "import x" into a relative import when a file named x.py
# sits next to the script being converted, which never applies here.
# fix_nonzero renames __nonzero__ to __bool__, which breaks classes that
# already define both; renpy/compat/legacy.py aliases them at runtime instead.
SKIP = ("fix_ws_comma", "fix_import", "fix_nonzero")


def rename_keywords(text):
    """
    Python 2 code can use names that are keywords in Python 3. The game uses
    a class called nonlocal; it becomes _nonlocal. Only plain names are
    touched, never text inside strings.
    """

    if "nonlocal" not in text:
        return text

    # Code that already compiles may be using the real nonlocal statement.
    try:
        compile("if True:\n" + text if text[:1] == " " else text, "<snippet>", "exec")
        return text
    except SyntaxError:
        pass

    import io
    import tokenize

    lines = text.splitlines(True)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))

    # Edit from the end so earlier positions stay valid.
    for t in reversed(tokens):
        if t.type == tokenize.NAME and t.string == "nonlocal":
            row, col = t.start
            line = lines[row - 1]
            lines[row - 1] = line[:col] + "_nonlocal" + line[col + len("nonlocal"):]

    return "".join(lines)


def convert(rt, text):
    text = rename_keywords(text.replace("\r", ""))

    # Indented blocks are wrapped the same way Ren'Py does it.
    indented = text[:1] == " "
    body = "if True:\n" + text if indented else text

    if not body.endswith("\n"):
        body += "\n"

    new = str(rt.refactor_string(body, "<snippet>"))

    if indented:
        new = new[len("if True:\n"):]

    if not text.endswith("\n") and new.endswith("\n"):
        new = new[:-1]

    return new


def main():
    dump, out = sys.argv[1:3]

    fixers = [ f for f in get_fixers_from_package("lib2to3.fixes") if f.rsplit(".", 1)[1] not in SKIP ]
    rt = RefactoringTool(fixers)

    snippets = { }

    with open(dump, encoding="utf-8") as f:
        for l in f:
            source, mode, py, filename, line = json.loads(l)

            # Only the game's own scripts; renpy/common is already Python 3.
            if filename.startswith("game/"):
                snippets.setdefault(source, filename)

    table = { }
    failed = collections.Counter()

    for source, filename in snippets.items():
        try:
            new = convert(rt, source)
        except Exception:
            failed[filename] += 1
            continue

        if new != source:
            table[source] = new

    with open(out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=0, sort_keys=True)

    print("%d snippets, %d converted, %d not parsed" % (len(snippets), len(table), sum(failed.values())))

    for filename, n in failed.most_common():
        print("  not parsed: %s (%d)" % (filename, n))


if __name__ == "__main__":
    main()
