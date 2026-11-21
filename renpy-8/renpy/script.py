# Copyright 2004-2026 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# This file contains code that is responsible for storing and executing a
# Ren'Py script.

from typing import Any

import __future__
import collections
import hashlib
import json
import os
import difflib
import time
import struct
import zlib
import sys
import shutil

import renpy

from renpy.compat.pickle import loads, dumps


# The version of the dumped script.
script_version = renpy.script_version

# The version of the bytecode cache.
BYTECODE_VERSION = 1

from importlib.util import MAGIC_NUMBER as PYC_MAGIC

# Change this to force a recompile of Python when required.
PYC_MAGIC += b"_2025-06-16"

# Change this to force a recompile of RPYC files when required, if the .rpy file exists.
RPYC_MAGIC = b"_2025-07-06"

# A string at the start of each rpycv2 file.
RPYC2_HEADER = b"RENPY RPC2"


# The name of the obsolete and new bytecode cache files.
OLD_BYTECODE_FILE = "cache/bytecode.rpyb"
BYTECODE_FILE = "cache/bytecode-{}{}.rpyb".format(sys.version_info.major, sys.version_info.minor)


# The on-demand script index (see Script.load_lazy_index).
LAZY_INDEX_FILE = "vita_lazy_scripts.json"

# Where the init-only copies of script files go in the game's archives.
INIT_ONLY_DIR = "vita_init"


class ScriptError(Exception):
    """
    Exception that is raised if the script is somehow inconsistent,
    or otherwise wrong.
    """


class LabelNotFound(ScriptError, LookupError):
    """
    Exception that is raised when a named node is not found.
    """

    def __init__(self, label: str | tuple[Any, ...]):
        super().__init__(f"could not find label '{label}'.")
        self.name = label

    def get_suggestion(self):
        if not isinstance(self.name, str):
            return None

        d = [node.name for node in renpy.game.script.namemap.values() if isinstance(node.name, str)]

        if self.name[:1] != "_":
            d = [x for x in d if x[:1] != "_"]

        if suggestion := renpy.error.compute_closest_value(self.name, d):
            return f" Did you mean: '{suggestion}'?"


def collapse_stmts(stmts):
    """
    Returns a flat list containing every statement in the tree
    stmts.
    """

    rv = []

    for i in stmts:
        i.get_children(rv.append)

    return rv


class Script(object):
    """
    This class represents a Ren'Py script, which is parsed out of a
    collection of script files. Once parsing and initial analysis is
    complete, this object can be serialized out and loaded back in,
    so it shouldn't change at all after that has happened.

    @ivar namemap: A map from the name of an AST node to the AST node
    itself.  This is used for jumps, calls, and to find the current
    node when loading back in a save. The names may be strings or
    integers, strings being explicit names provided by the user, and
    integers being names synthesised by renpy.

    @ivar initcode: A list of priority, Node tuples that should be
    executed in ascending priority order at init time.

    @ivar all_stmts: A list of all statements, that have been found
    in every file. Useful for lint, but tossed if lint is not performed
    to save memory.

    """

    def __init__(self):
        """
        Loads the script by parsing all of the given files, and then
        walking the various ASTs to initialize this Script object.
        """

        # Set us up as renpy.game.script, so things can use us while
        # we're loading.
        renpy.game.script = self

        if os.path.exists(renpy.config.renpy_base + "/lock.txt"):
            with open(renpy.config.renpy_base + "/lock.txt", "rb") as f:
                self.key = f.read()
        else:
            self.key = None

        self.namemap = {}
        self.all_stmts = []
        self.all_pycode = []
        self.all_pyexpr = []

        # A list of statements that haven't been analyzed.
        self.need_analysis = []

        # A list of statements that need to be compressed.
        self.need_compress = []

        self.record_pycode = True

        # Bytecode caches.
        self.bytecode_oldcache = {}
        self.bytecode_newcache = {}
        self.bytecode_dirty = False

        self.translator = renpy.translation.ScriptTranslator()
        self.init_bytecode()

        # Scripts loaded on demand (see load_lazy_index): file name -> dir,
        # label -> file name, and the file name statements carry -> file name.
        self.lazy_files = {}
        self.lazy_labels = {}
        self.lazy_by_filename = {}

        # Files whose init code ran at startup, with the rest left for later.
        self.lazy_stripped = set()

        self.scan_script_files()

        # If recompiling everything, remove orphan .rpyc files.
        # Otherwise, will fail in case orphan .rpyc have same
        # labels as in other scripts (usually happens on script rename).
        if (renpy.game.args.command == "compile") and not (renpy.game.args.keep_orphan_rpyc):

            self.clean_script_files()

            # Reindex files so that .rpyc's are cleared out.
            renpy.loader.index_files()
            self.scan_script_files()

        self.translator.chain_translates()

        self.compress()

        self.serial = 0

        self.digest = hashlib.md5(renpy.version_only.encode("utf-8"))

        self.loaded_rpy = False
        self.backup_list = []

        self.duplicate_labels = []

        # A list of (initcode priority, statement) pairs.
        self.initcode: list[tuple[int, renpy.ast.Node]] = []

        # A set of (fn, dir) tuples for scripts that have already been
        # loaded.
        self.loaded_scripts = set()

        # A set of languages to load.
        self.load_languages = set()

    def choose_backupdir(self):
        if renpy.mobile:
            return None

        for i in ["script_version.txt", "script_version.rpy", "script_version.rpyc"]:
            if renpy.loader.loadable(i):
                return None

        backups = renpy.__main__.path_to_saves(renpy.config.gamedir, "backups")

        if backups is None:
            return

        basename = os.path.basename(renpy.config.basedir)
        backupdir = renpy.os.path.join(renpy.exports.fsencode(backups), renpy.exports.fsencode(basename))

        renpy.exports.write_log("Backing up script files to %r:", backupdir)

        return backupdir

    def make_backups(self):
        backup_list = self.backup_list
        self.backup_list = []

        if os.environ.get("RENPY_DISABLE_BACKUPS", "") == "I take responsibility for this.":
            return

        if not self.loaded_rpy:
            return

        if renpy.mobile:
            return

        backupdir = self.choose_backupdir()
        if backupdir is None:
            return

        for fn, checksum in backup_list:
            if not fn.startswith(renpy.config.gamedir):
                continue

            if not os.path.exists(fn):
                continue

            short_fn = renpy.exports.fsencode(fn[len(renpy.config.gamedir) + 1 :])

            base, ext = os.path.splitext(short_fn)

            hex_checksum = checksum[:8].hex()

            target_fn = os.path.join(
                backupdir,
                base + "." + hex_checksum + ext,
            )

            if os.path.exists(target_fn):
                continue

            try:
                os.makedirs(os.path.dirname(target_fn), 0o700)  # type: ignore
            except Exception:
                pass

            try:
                shutil.copy(fn, target_fn)  # type: ignore
            except Exception:
                pass

    def classify_script_files(self, dirlist, script_files, module_files):
        """
        Classify the files in dirlist as script or module files, or not script related.
        """

        for dir, fn in dirlist:

            if fn.rpartition("/")[2].startswith("."):
                continue

            if fn.endswith("_ren.py"):
                if dir is None:
                    continue

                fn = fn[:-7]
                target = script_files

            elif fn.endswith(".rpy"):
                if dir is None:
                    continue

                fn = fn[:-4]
                target = script_files

            elif fn.endswith(".rpyc"):
                fn = fn[:-5]
                target = script_files

            elif fn.endswith(".rpym"):
                if dir is None:
                    continue

                fn = fn[:-5]
                target = module_files
            elif fn.endswith(".rpymc"):
                fn = fn[:-6]
                target = module_files
            else:
                continue

            if (fn, dir) not in target:
                target.append((fn, dir))

    def scan_script_files(self):
        """
        Scan the directories for script files.
        """

        # A list of all files in the search directories.
        dirlist = renpy.loader.listdirfiles()

        # A list of directory, filename w/o extension pairs. This is
        # what we will load immediately.
        self.script_files = []

        # Similar, but for files in the common directory.
        self.common_script_files = []

        # Similar, but for modules:
        self.module_files = []

        self.classify_script_files(
            renpy.loader.listdirfiles(common=True, game=False), self.common_script_files, self.module_files
        )

        self.classify_script_files(
            renpy.loader.listdirfiles(common=False, game=True), self.script_files, self.module_files
        )


    def clean_script_files(self):
        """
        Delete .rpyc files that have no corresponding .rpy or _ren.py file.
        """

        for fn, dn in self.script_files:
            if dn is None:
                continue

            if not os.path.isfile(os.path.join(dn, fn + ".rpy")) and not os.path.isfile(
                os.path.join(dn, fn + "_ren.py")
            ):
                try:
                    name = os.path.join(dn, fn + ".rpyc")
                    os.rename(name, name + ".bak")
                except OSError:
                    # This perhaps shouldn't happen since either .rpy or .rpyc should exist
                    pass

    def script_filter(self, fn, dir):
        """
        This determines if a script file should be loaded.
        during this call to load_script.
        """

        if not renpy.config.defer_tl_scripts:
            return True

        if (renpy.game.args.command != "run") or renpy.game.args.compile or renpy.game.args.lint:
            return True

        parts = fn.split("/")

        if parts[0] == "tl":
            if len(parts) <= 2:
                return True

            if parts[1] == "None":
                return True

            if parts[1] in self.load_languages:
                return True

            return False

        return True

    def sort_script_files(self):
        """
        Sorts the script files in the order they should be loaded.
        """

        self.common_script_files.sort(key=lambda item: ((item[0] or ""), (item[1] or "")))

        has_libs = renpy.loader.loadable("libs/libs.txt")
        has_mods = renpy.loader.loadable("mods/mods.txt")

        def game_key(item):
            fn = item[0] or ""
            dn = item[1] or ""

            priority = 1
            sort_key = fn

            parts = fn.split("/")

            if has_libs and fn.startswith("libs/"):
                priority = 0

                if len(parts) > 2:
                    sort_key = "/".join(parts[2:])
                else:
                    sort_key = parts[1]

            if has_mods and fn.startswith("mods/"):
                priority = 2

                if len(parts) > 2:
                    sort_key = "/".join(parts[2:])
                else:
                    sort_key = parts[1]

            priority *= 2
            if fn >= "A":
                priority += 1

            return (priority, sort_key, fn, dn)

        self.script_files.sort(key=game_key)

        rv = [ (0,) + item for item in self.common_script_files ]
        rv.extend((game_key(item)[0],) + item for item in self.script_files)

        return rv

    def load_lazy_index(self):
        """
        On the Vita, script files that only hold story (no init code) are
        loaded the first time one of their labels or statements is needed,
        not at startup: a game's whole script doesn't fit in memory. The
        index of those files is made on the PC by starting the game with
        RENPY_LAZY_INDEX set (see index_file).
        """

        # Not when a new index is being made.
        if not renpy.vita or os.environ.get("RENPY_LAZY_INDEX"):
            return {}

        try:
            with renpy.loader.load(LAZY_INDEX_FILE) as f:
                index = json.loads(f.read().decode("utf-8"))
        except Exception:
            return {}

        for label, fn in index["labels"].items():
            self.lazy_labels[label] = fn

        for fn, (filename, _has_init) in index["files"].items():
            self.lazy_by_filename[filename] = fn

        # Ren'Py's list of every statement is only used by lint.
        self.all_stmts = None

        return index["files"]

    def load_script(self):
        script_files = self.sort_script_files()

        initcode = []

        lazy = self.load_lazy_index()

        index_out = os.environ.get("RENPY_LAZY_INDEX")
        index_files = {}
        index_labels = {}

        count = 0
        skipped = 0

        last_priority = 0

        for priority, fn, dir in script_files:

            if priority != last_priority:
                if renpy.parser.has_parse_errors():
                    skipped += len(script_files) - count
                    break

            count += 1
            renpy.display.presplash.progress("Loading script...", count, len(script_files))

            # Pump the presplash window to prevent marking
            # our process as unresponsive by OS
            renpy.display.presplash.pump_window()

            if (fn, dir) in self.loaded_scripts:
                continue

            # Init-only copies load in place of their file (load_init_only).
            if fn.startswith(INIT_ONLY_DIR + "/"):
                continue

            if not self.script_filter(fn, dir):
                skipped += 1
                continue

            self.loaded_scripts.add((fn, dir))

            if fn in lazy:
                self.lazy_files[fn] = dir

                if lazy[fn][1]:
                    self.load_init_only(fn, dir, initcode)
                    self.lazy_stripped.add(fn)

                continue

            if index_out:
                self.index_file(fn, dir, initcode, index_files, index_labels)
                continue

            self.load_appropriate_file(".rpyc", ["_ren.py", ".rpy"], dir, fn, initcode)

        if skipped:
            renpy.display.log.write("{} script files skipped.".format(skipped))

        initcode.sort(key=lambda i: i[0])

        self.initcode.extend(initcode)

        self.translator.chain_translates()

        self.compress()

        if index_out:
            with open(index_out, "w", encoding="utf-8") as f:
                json.dump({"files": index_files, "labels": index_labels}, f, sort_keys=True)

            renpy.display.log.write("Wrote %s: %d script files can load on demand.", index_out, len(index_files))

        if self.lazy_files:
            renpy.display.log.write("%d script files will load when first needed.", len(self.lazy_files))

        return initcode

    def index_file(self, fn, dir, initcode, index_files, index_labels):
        """
        Loads a script file, and records it in the on-demand index if it can
        be loaded later: it adds no init code and runs nothing at load time.
        """

        before_init = len(initcode)
        all_stmts = self.all_stmts
        self.all_stmts = []

        try:
            self.load_appropriate_file(".rpyc", ["_ren.py", ".rpy"], dir, fn, initcode)
            stmts = self.all_stmts
        finally:
            if all_stmts is not None:
                all_stmts.extend(self.all_stmts)

            self.all_stmts = all_stmts

        # Ren'Py's own common scripts always load normally.
        if (fn, dir) in self.common_script_files or not stmts:
            return

        has_init = any(self.does_init(node) for _priority, node in initcode[before_init:])

        for i in stmts:
            if isinstance(i, (renpy.ast.EarlyPython, renpy.ast.TranslateBlock, renpy.ast.Testcase)):
                return

        filenames = set(i.filename for i in stmts)

        if len(filenames) != 1:
            return

        index_files[fn] = [filenames.pop(), has_init]

        init_dir = os.environ.get("RENPY_LAZY_INIT_DIR")

        if has_init and init_dir:
            self.write_init_only(fn, initcode[before_init:], init_dir)

        for i in stmts:
            if isinstance(i.name, str):
                index_labels[i.name] = fn

    def does_init(self, node):
        """
        Returns False for init nodes that do nothing at init time. Scripts
        compiled by Ren'Py 7 give every user statement (pause, call screen
        ...) an init priority, though most have no init function.
        """

        if not isinstance(node, renpy.ast.UserStatement):
            return True

        try:
            node.get_name()

            return any(renpy.statements.get(i, node.parsed) for i in ("execute_init", "execute_default", "init"))
        except Exception:
            return True

    def write_init_only(self, fn, initcode, init_dir):
        """
        Writes a .rpyc holding only the nodes in `initcode` that do something
        at init time, so the Vita can load a file's init code without the
        story around it (see load_init_only).
        """

        nodes = [ n for _priority, n in initcode if self.does_init(n) ]

        # Their links into the rest of the file would pull it all in.
        saved = [ (n, n.next) for n in nodes ]

        try:
            for n in nodes:
                n.next = None

            data = { "version": script_version, "key": self.key or "unlocked" }
            pickled = dumps((data, nodes))
        except Exception:
            # Some files hold objects that can't be pickled once loaded;
            # those are stripped at startup instead.
            return
        finally:
            for n, next_node in saved:
                n.next = next_node

        path = os.path.join(init_dir, fn + ".rpyc")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "wb") as f:
            self.write_rpyc_header(f)
            self.write_rpyc_data(f, 1, pickled)
            self.write_rpyc_md5(f, hashlib.md5(pickled).digest())

    def load_init_only(self, fn, dir, initcode):
        """
        Loads a script file for its init code only. Everything else in it
        (labels, dialogue) is dropped again, and comes back when the file is
        loaded on demand.
        """

        before = len(initcode)

        init_fn = INIT_ONLY_DIR + "/" + fn

        if renpy.loader.loadable(init_fn + ".rpyc"):
            # Made on the PC by write_init_only: nothing to strip.
            self.load_appropriate_file(".rpyc", ["_ren.py", ".rpy"], None, init_fn, initcode)
            return

        all_stmts = self.all_stmts
        self.all_stmts = []

        try:
            self.load_appropriate_file(".rpyc", ["_ren.py", ".rpy"], dir, fn, initcode)
            stmts = self.all_stmts
        finally:
            self.all_stmts = all_stmts

        # Ren'Py 7 user statements (pause, call screen ...) are init nodes
        # that do nothing at init.
        real = [ (p, n) for p, n in initcode[before:] if self.does_init(n) ]
        initcode[before:] = real

        keep = set()

        for _priority, node in real:
            node.get_children(lambda c: keep.add(id(c)))

        dropped = [ n for n in stmts if id(n) not in keep ]
        dropped_ids = set(id(n) for n in dropped)

        for n in dropped:
            if self.namemap.get(n, None) is n:
                del self.namemap[n]

        # Init code runs node by node, so its links into the story aren't
        # needed (and would keep it in memory).
        for _priority, node in real:
            node.next = None

        tr = self.translator

        for n in dropped:
            identifier = getattr(n, "identifier", None)

            if identifier is not None and getattr(n, "language", 0) is None:
                if tr.default_translates.get(identifier, None) is n:
                    del tr.default_translates[identifier]

        if stmts:
            filename = os.path.normpath(os.path.abspath(renpy.lexer.unelide_filename(stmts[0].filename)))

            if filename in tr.file_translates:
                tr.file_translates[filename] = [ i for i in tr.file_translates[filename] if id(i[1]) not in dropped_ids ]

        self.need_analysis = [ n for n in self.need_analysis if id(n) not in dropped_ids ]
        self.need_compress = [ n for n in self.need_compress if id(n) not in dropped_ids ]

    def load_lazy_file(self, fn):
        """
        Loads one of the files left out at startup by load_lazy_index.
        """

        dir = self.lazy_files[fn]

        initcode = []

        try:
            self.load_appropriate_file(".rpyc", ["_ren.py", ".rpy"], dir, fn, initcode)
        except Exception:
            import traceback

            renpy.display.log.write("Loading %s on demand failed: %s", fn, traceback.format_exc())
            raise

        del self.lazy_files[fn]

        if renpy.vita:
            renpy.vitasupport.freeze_objects()

        self.translator.chain_translates()
        self.compress()
        self.analyze()

        for label, lfn in list(self.lazy_labels.items()):
            if lfn == fn:
                del self.lazy_labels[label]

        # A stripped file's init code already ran at startup.
        if fn not in self.lazy_stripped and any(self.does_init(node) for _priority, node in initcode):
            renpy.display.log.write("Warning: %s has init code but was loaded on demand.", fn)

    def load_lazy(self, name):
        """
        If `name` (a label or a statement name) is in a script file that
        hasn't been loaded yet, loads it. Returns True if it did.
        """

        if not self.lazy_files:
            return False

        # Files can't be opened while predicting; the story loads it when it
        # actually gets there.
        if renpy.display.predict.predicting:
            return False

        if isinstance(name, str):
            fn = self.lazy_labels.get(name, None)
        elif isinstance(name, tuple) and name and isinstance(name[0], str):
            fn = self.lazy_by_filename.get(name[0], None)
        else:
            fn = None

        if fn is None or fn not in self.lazy_files:
            return False

        self.load_lazy_file(fn)
        return True

    def load_module(self, name):
        files = [(fn, dir) for fn, dir in self.module_files if fn == name]

        if not files:
            raise Exception("Module %s could not be loaded." % name)

        if len(files) > 2:
            raise Exception("Module %s ambiguous, multiple variants exist." % name)

        fn, dir = files[0]
        initcode = []

        self.load_appropriate_file(".rpymc", [".rpym"], dir, fn, initcode)

        if renpy.parser.report_parse_errors():
            raise SystemExit(-1)

        initcode.sort(key=lambda i: i[0])

        self.translator.chain_translates()

        self.compress()

        return initcode

    def include_module(self, name):
        """
        Loads a module with the provided name and inserts its
        initcode into the script current initcode
        """

        module_initcode = self.load_module(name)
        if not module_initcode:
            return

        # We may not insert elements at or prior the current id!
        current_id = renpy.game.initcode_ast_id

        if module_initcode[0][0] < self.initcode[current_id][0]:
            raise Exception("Module %s contains nodes with priority lower than the node that loads it" % name)

        merge_id = current_id + 1
        current_tail = self.initcode[merge_id:]

        # Since script initcode and module initcode are both sorted,
        # we can use heap to merge them
        new_tail = current_tail + module_initcode
        new_tail.sort(key=lambda i: i[0])

        self.initcode[merge_id:] = new_tail

    def assign_names(self, stmts, fn):
        # Assign names to statements that don't have one already.

        all_stmts = collapse_stmts(stmts)

        version = int(time.time() * 100) & 0xFFFFFFFF

        old_names = set(s.name for s in all_stmts)

        for s in all_stmts:
            if s.name is None:
                while True:
                    name = (fn, version, self.serial)
                    self.serial += 1

                    if name not in old_names:
                        break

                s.name = name

    def merge_names(self, old_stmts, new_stmts, used_names):
        old_stmts = collapse_stmts(old_stmts)
        new_stmts = collapse_stmts(new_stmts)

        old_info = [i.diff_info() for i in old_stmts]
        new_info = [i.diff_info() for i in new_stmts]

        sm = difflib.SequenceMatcher(None, old_info, new_info)

        for oldl, newl, count in sm.get_matching_blocks():
            for i in range(count):
                old = old_stmts[oldl + i]
                new = new_stmts[newl + i]

                if (new.name is None) and (old.name not in used_names):
                    new.name = old.name
                    used_names.add(new.name)

    def load_string(self, filename, filedata, linenumber=1):
        """
        Loads Ren'Py script from a string.

        `filename`
            The filename that's assigned to the data.

        `filedata`
            A unicode string to be loaded.

        Return the list of statements making up the root block, and a
        list of init statements that need to be run.
        """

        stmts = renpy.parser.parse(filename, filedata, linenumber=linenumber)

        if stmts is None:
            return None, None

        renpy.parser.release_deferred_errors()
        if renpy.parser.parse_errors:
            return None, None

        self.assign_names(stmts, filename)
        self.static_transforms(stmts)

        initcode = []

        stmts = self.finish_load(stmts, initcode, False)

        initcode.sort(key=lambda i: i[0])

        return stmts, initcode

    def finish_load(self, stmts, initcode, check_names=True, filename=None):
        """
        Given `stmts`, a list of AST nodes comprising the root block,
        finishes loading it.

        `initcode`
            A list we append init statements to.

        `check_names`
            If true, produce duplicate name errors.

        `filename`
            If given, a filename that overrides the filename found inside the
            file.

        Returns a list of statements that corresponds to the top-level block
        in initcode after transformation.
        """

        if not stmts:
            return stmts

        # Chain together the statements in the file.
        renpy.ast.chain_block(stmts, None)

        # All of the statements found in file, regardless of nesting
        # depth.

        all_stmts: list[renpy.ast.Node] = []

        for i in stmts:
            i.get_children(all_stmts.append)

        for i in all_stmts:
            if isinstance(i, renpy.ast.RPY):
                a, b = i.rest
                if a == "python":
                    if b == "3":
                        b = "division"
                    if b in __future__.all_feature_names:
                        renpy.python.file_compiler_flags[i.filename] |= getattr(__future__, b).compiler_flag
                    else:
                        raise Exception("Unknown __future__ : {!r}.".format(b))

        # Take the translations.
        self.translator.take_translates(all_stmts)

        # Fix the filename for a renamed .rpyc file. (Init-only copies keep
        # the name of the file they came from.)
        if filename is not None and not filename.startswith(INIT_ONLY_DIR + "/"):
            filename = renpy.lexer.elide_filename(filename)
            if filename[-1] == "c":
                filename = filename[:-1]

            if not all_stmts[0].filename.lower().endswith(filename.lower()):
                filename += "c"

                for i in all_stmts:
                    old_name = i.name
                    i.filename = filename
                    i.name = old_name

        def check_name(node):
            if not check_names:
                return

            if renpy.mobile:
                return

            bad_name = None
            bad_node = None
            old_node = None

            name = node.name

            if name in self.namemap:
                bad_name = name
                bad_node = node
                old_node = self.namemap[name]

                if not isinstance(bad_name, str):
                    raise ScriptError(
                        "Name %s is defined twice, at %s:%d and %s:%d."
                        % (
                            repr(bad_name),
                            old_node.filename,
                            old_node.linenumber,
                            bad_node.filename,
                            bad_node.linenumber,
                        )
                    )

                else:
                    if renpy.config.allow_duplicate_labels:
                        return

                    import linecache

                    self.duplicate_labels.append(
                        'The label {} is defined twice, at File "{}", line {}:\n{}and File "{}", line {}:\n{}'.format(
                            bad_name,
                            old_node.filename,
                            old_node.linenumber,
                            linecache.getline(old_node.filename, old_node.linenumber),
                            bad_node.filename,
                            bad_node.linenumber,
                            linecache.getline(bad_node.filename, bad_node.linenumber),
                        )
                    )

        self.update_bytecode()

        for node in all_stmts:
            name = node.name

            if isinstance(node, renpy.ast.Testcase):
                # Handle testcases specially, as they are not part of the script.
                renpy.test.testexecution.register_testcase(node.test)

            else:
                check_name(node)

                # Add the name to the namemap. This uses the node as both key and value, as the node is equal to and
                # hashes as its name.
                self.namemap[node] = node

            # Add any init nodes to self.initcode.
            if (priority := node.get_init()) is not None:
                initcode.append((priority, node))

            node.early_execute()

        if self.all_stmts is not None:
            self.all_stmts.extend(all_stmts)

        self.need_analysis.extend(all_stmts)
        self.need_compress.extend(all_stmts)

        return stmts

    def write_rpyc_header(self, f):
        """
        Writes an empty version 2 .rpyc header to the open binary file `f`.
        """

        f.write(RPYC2_HEADER)

        for _i in range(3):
            f.write(struct.pack("III", 0, 0, 0))

    def write_rpyc_data(self, f, slot, data):
        """
        Writes data into `slot` of a .rpyc file. The data should be a binary
        string, and is compressed before being written.
        """

        f.seek(0, 2)

        start = f.tell()
        data = zlib.compress(data, 3)
        f.write(data)

        f.seek(len(RPYC2_HEADER) + 12 * (slot - 1), 0)
        f.write(struct.pack("III", slot, start, len(data)))

        f.seek(0, 2)

    def write_rpyc_md5(self, f, digest):
        """
        Writes the md5 to the end of a .rpyc file.
        """

        f.seek(0, 2)
        f.write(digest)

    def read_rpyc_data(self, f, slot):
        """
        Reads the binary data from `slot` in a .rpyc (v1 or v2) file. Returns
        the data if the slot exists, or None if the slot does not exist.
        """

        # f.seek(0)
        header_data = f.read(1024)

        # header = f.read(len(RPYC2_HEADER))

        # Legacy path.
        if header_data[: len(RPYC2_HEADER)] != RPYC2_HEADER:
            if slot != 1:
                return None

            f.seek(0)
            data = f.read()

            return zlib.decompress(data)

        # RPYC2 path.
        pos = len(RPYC2_HEADER)

        while True:
            header_slot, start, length = struct.unpack("III", header_data[pos : pos + 12])

            if slot == header_slot:
                break

            if header_slot == 0:
                return None

            pos += 12

        f.seek(start)
        data = f.read(length)

        return zlib.decompress(data)

    def static_transforms(self, stmts):
        """
        This performs transformations on the script that can be performed
        statically. When possible, these transforms are stored in slot 2
        of the rpyc file.
        """

        # Generate translate nodes.
        renpy.translation.restructure(stmts)

    def load_file(self, dir, fn):
        # Used to only find the deferred parse errors from this file.
        old_deferred_parse_errors = renpy.parser.deferred_parse_errors
        renpy.parser.deferred_parse_errors = collections.defaultdict(list)

        try:
            if fn.endswith(".rpy") or fn.endswith(".rpym") or fn.endswith("_ren.py"):
                if not dir:
                    raise Exception("Cannot load rpy/rpym/ren.py file %s from inside an archive." % fn)

                base, _, game = dir.replace("\\", "/").rpartition("/")
                olddir = base + "/old-" + game

                fullfn = dir + "/" + fn

                if fn.endswith("_ren.py"):
                    rpycfn = fullfn[:-7] + ".rpyc"
                    oldrpycfn = olddir + "/" + fn[:-7] + ".rpyc"
                else:
                    rpycfn = fullfn + "c"
                    oldrpycfn = olddir + "/" + fn + "c"

                stmts = renpy.parser.parse(fullfn)

                data = {}
                data["version"] = script_version
                data["key"] = self.key or "unlocked"
                data["deferred_parse_errors"] = renpy.parser.deferred_parse_errors

                if stmts is None:
                    return data, []

                used_names = set()

                for mergefn in [oldrpycfn, rpycfn]:
                    old_all_pyexpr = self.all_pyexpr
                    self.record_pycode = False
                    self.all_pyexpr = None

                    # See if we have a corresponding .rpyc file. If so, then
                    # we want to try to upgrade our .rpy file with it.
                    try:
                        with open(mergefn, "rb") as rpycf:
                            bindata = self.read_rpyc_data(rpycf, 1)

                        if bindata is not None:
                            old_data, old_stmts = loads(bindata)
                            self.merge_names(old_stmts, stmts, used_names)

                            del old_data
                            del old_stmts
                    except Exception:
                        pass
                    finally:
                        self.record_pycode = True
                        self.all_pyexpr = old_all_pyexpr

                self.assign_names(stmts, renpy.lexer.elide_filename(fullfn))

                pickle_data_before_static_transforms = dumps(
                    (data, stmts),
                    bad_reduction_name=f"<{fn} rpyc data>",
                )

                self.static_transforms(stmts)

                pickle_data_after_static_transforms = dumps(
                    (data, stmts),
                    bad_reduction_name=f"<{fn} transformed rpyc data>",
                )

                if not renpy.macapp:
                    try:
                        with open(rpycfn, "wb") as f:
                            self.write_rpyc_header(f)
                            self.write_rpyc_data(f, 1, pickle_data_before_static_transforms)
                            self.write_rpyc_data(f, 2, pickle_data_after_static_transforms)

                            with open(fullfn, "rb") as fullf:
                                rpydigest = hashlib.md5(fullf.read() + RPYC_MAGIC).digest()

                            self.write_rpyc_md5(f, rpydigest)
                    except Exception:
                        import traceback

                        traceback.print_exc()

                self.loaded_rpy = True

            elif fn.endswith(".rpyc") or fn.endswith(".rpymc"):
                data = None
                stmts = None

                with renpy.loader.load(fn, tl=False) as f:
                    for slot in [2, 1]:
                        try:
                            bindata = self.read_rpyc_data(f, slot)

                            if bindata:
                                try:
                                    data, stmts = loads(bindata)
                                except Exception as e:
                                    print(f"Failed to load {fn}: {e}")
                                    raise

                                break

                        except MemoryError:
                            # Don't report running out of memory as a
                            # damaged file.
                            raise

                        except Exception:
                            pass

                        f.seek(0)

                    else:
                        return None, None

                    if data is None:
                        print("Failed to load", fn)
                        return None, None

                    if not isinstance(data, dict):
                        return None, None

                    if self.key and data.get("key", "unlocked") != self.key:
                        return None, None

                    if data["version"] != script_version:
                        return None, None

                    if slot < 2:
                        self.static_transforms(stmts)

                    renpy.parser.deferred_parse_errors = data.get(
                        "deferred_parse_errors", None
                    ) or collections.defaultdict(list)

            else:
                return None, None

            return data, stmts

        finally:
            # Restore the deferred parse errors.
            for k, v in renpy.parser.deferred_parse_errors.items():
                old_deferred_parse_errors[k].extend(v)

            renpy.parser.deferred_parse_errors = old_deferred_parse_errors

    def load_appropriate_file(self, compiled, source_extensions, dir, fn, initcode):
        data = None

        source = source_extensions[-1]

        renpy.game.exception_info = "While loading the script."

        # This can only be a .rpyc file, since we're loading it
        # from an archive.
        if dir is None:
            rpyfn = fn + source
            lastfn = fn + compiled
            data, stmts = self.load_file(dir, fn + compiled)

            if data is None:
                raise Exception("Could not load from archive %s." % (lastfn,))

            with renpy.loader.load(fn + compiled, tl=False) as f:
                f.seek(-hashlib.md5().digest_size, 2)
                digest = f.read(hashlib.md5().digest_size)

        else:
            # Otherwise, we're loading from disk. So we need to decide if
            # we want to load the rpy or the rpyc file.
            rpycfn = dir + "/" + fn + compiled
            rpyfn = None  # prevent the spurious warning.
            rpydigest = None

            rpyfns = []

            for source in source_extensions:
                rpyfn = dir + "/" + fn + source

                renpy.loader.add_auto(rpyfn)

                if os.path.exists(rpyfn):
                    rpyfns.append((source, rpyfn))

            if len(rpyfns) > 1:
                raise Exception(
                    "{} conflict, and can't exist in the same game.".format(" and ".join(i[1] for i in rpyfns))
                )
            elif rpyfns:
                source, rpyfn = rpyfns[0]

                with open(rpyfn, "rb") as f:
                    rpydigest = hashlib.md5(f.read() + RPYC_MAGIC).digest()
            else:
                source = source_extensions[-1]
                rpyfn = dir + "/" + fn + source_extensions[-1]

            try:
                if os.path.exists(rpycfn):
                    with open(rpycfn, "rb") as f:
                        f.seek(-hashlib.md5().digest_size, 2)
                        rpycdigest = f.read(hashlib.md5().digest_size)
                else:
                    rpycdigest = None
            except Exception:
                rpycdigest = None

            digest = None

            if os.path.exists(rpyfn) and os.path.exists(rpycfn):
                # Are we forcing a compile?
                force_compile = renpy.game.args.compile  # type: ignore

                # Use the source file here since it'll be loaded if it exists.
                lastfn = rpyfn

                data, stmts = None, None

                try:
                    if rpydigest == rpycdigest and not force_compile:
                        data, stmts = self.load_file(dir, fn + compiled)

                        if data is None:
                            print("Could not load " + rpycfn)

                except Exception:
                    renpy.display.log.write("While loading %r", rpycfn)
                    renpy.display.log.exception()

                    if "RENPY_RPYC_EXCEPTIONS" in os.environ:
                        print("While loading", rpycfn)
                        raise

                if data is None:
                    data, stmts = self.load_file(dir, fn + source)

                digest = rpydigest

            elif os.path.exists(rpycfn):
                lastfn = rpycfn
                data, stmts = self.load_file(dir, fn + compiled)

                digest = rpycdigest

            elif os.path.exists(rpyfn):
                lastfn = rpyfn
                data, stmts = self.load_file(dir, fn + source)

                digest = rpydigest

            if digest is not None:
                self.backup_list.append((rpyfn, digest))

        if data is None:
            raise Exception("Could not load file %s." % lastfn)  # type: ignore

        # Check the key.
        if self.key is None:
            self.key = data["key"]
        elif self.key != data["key"]:
            raise Exception(
                fn
                + " does not share a key with at least one .rpyc file. To fix, delete all .rpyc files, or rerun Ren'Py with the --lock option."
            )

        self.finish_load(stmts, initcode, filename=lastfn)  # type: ignore

        self.digest.update(digest)  # type: ignore

    def init_bytecode(self):
        """
        Init/Loads the bytecode cache.
        """

        if renpy.game.args.compile_python:
            return

        # Load the oldcache.
        try:
            with renpy.loader.load(BYTECODE_FILE) as f:
                version, cache = loads(zlib.decompress(f.read()))
                if version == BYTECODE_VERSION:
                    self.bytecode_oldcache = cache

        except Exception:
            pass

    def update_bytecode(self):
        """
        Compiles the PyCode objects in self.all_pycode, updating the
        cache. Clears out self.all_pycode.
        """

        renpy.python.compile_warnings = []

        # Used by tools/make_py3_fixes.py to collect the game's Python.
        dump = os.environ.get("RENPY_DUMP_PYCODE")

        if dump:
            import json

            with open(dump, "a", encoding="utf-8") as f:
                for i in self.all_pyexpr:
                    f.write(json.dumps([str(i), "eval", i.py, i.filename, i.linenumber]) + "\n")

                for i in self.all_pycode:
                    f.write(json.dumps([str(i.source), i.mode, i.py, i.filename, i.linenumber]) + "\n")

        # On the Vita, expressions are compiled the first time they're used
        # (from the bytecode cache shipped with the game) instead of all at
        # once here: keeping tens of thousands of compiled expressions around
        # doesn't fit in the Vita's memory. The dump above still sees them.
        # RENPY_COMPILE_ALL compiles them anyway, to build that cache on the PC.
        if not renpy.vita or os.environ.get("RENPY_COMPILE_ALL"):
            for i in self.all_pyexpr:
                try:
                    renpy.python.py_compile(i, "eval")
                except Exception:
                    pass

        self.all_pyexpr = []

        # Update all of the PyCode objects in the system with the loaded
        # bytecode.

        old_ei = renpy.game.exception_info

        for i in self.all_pycode:
            try:
                renpy.game.exception_info = "While compiling python block starting at line %d of %s." % (
                    i.linenumber,
                    i.filename,
                )

                i.bytecode = renpy.python.py_compile(
                    i.source,
                    i.mode,
                    filename=i.filename,
                    lineno=i.linenumber,
                    py=i.py,
                    hashcode=i.hashcode,
                    column=i.col_offset,
                )

            except SyntaxError as e:
                assert e.filename is not None
                assert e.lineno is not None
                pem = renpy.parser.ParseError(
                    e.msg,
                    e.filename,
                    e.lineno,
                    e.offset + i.col_offset,
                    e.text,
                    e.end_lineno,
                    e.end_offset + i.col_offset,
                )

                renpy.parser.parse_errors.append(pem.message)

            finally:
                renpy.game.exception_info = old_ei

        self.all_pycode = []

    def save_bytecode(self):
        if renpy.macapp:
            return

        if self.bytecode_dirty:
            try:
                fn = renpy.loader.get_path(BYTECODE_FILE)

                cache = self.bytecode_newcache

                # Expressions are compiled lazily on the Vita (see
                # update_bytecode), so most of the old cache was never
                # touched this session. Keep it.
                if renpy.vita:
                    cache = dict(self.bytecode_oldcache)
                    cache.update(self.bytecode_newcache)

                with open(fn, "wb") as f:
                    data = (BYTECODE_VERSION, cache)
                    f.write(zlib.compress(dumps(data), 3))
            except Exception:
                pass

            fn = renpy.loader.get_path(OLD_BYTECODE_FILE)
            try:
                os.unlink(fn)
            except Exception:
                pass

    def lookup(self, label):
        """
        Looks up the given label in the game. If the label is not found,
        raises a LabelNotFound.
        """

        if isinstance(label, renpy.parser.SubParse):
            label = label.block[0].name

        label = renpy.config.label_overrides.get(label, label)
        original = label

        rv = self.namemap.get(label, None)

        if (rv is None) and self.load_lazy(label):
            rv = self.namemap.get(label, None)

        if (rv is None) and (renpy.config.missing_label_callback is not None):
            label = renpy.config.missing_label_callback(label)
            rv = self.namemap.get(label, None)

        if rv is None:
            raise LabelNotFound(original)

        return self.namemap[label]

    def has_label(self, label):
        """
        Returns true if the label exists, or false otherwise.
        """

        if isinstance(label, renpy.parser.SubParse):
            if not label.block:
                return False

            label = label.block[0].name

        label = renpy.config.label_overrides.get(label, label)

        if label in self.namemap:
            return True

        # Labels in files that haven't been loaded yet.
        if isinstance(label, str):
            return label in self.lazy_labels

        return self.load_lazy(label) and label in self.namemap

    def lookup_or_none(self, label):
        """
        Looks up the label if it exists, or returns None if it does not.
        """

        if label is None:
            return None

        if not self.has_label(label):
            return None

        return self.lookup(label)

    def analyze(self):
        """
        Analyzes all statements that need analysis.
        """

        for i in self.need_analysis:
            i.analyze()

        self.need_analysis = []

    def compress(self):
        """
        Compress all statements that need to be compressed.
        """

        for i in self.need_compress:
            i._compress()

        self.need_compress = []

    def report_duplicate_labels(self):
        if not renpy.config.developer:
            return

        if renpy.config.ignore_duplicate_labels:
            return

        renpy.parser.parse_errors = self.duplicate_labels

        if renpy.parser.report_parse_errors():
            raise SystemExit(-1)

    def __del__(self):
        for v in self.namemap.values():
            v._kill()
