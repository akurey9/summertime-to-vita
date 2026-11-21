# Support for running scripts compiled by Ren'Py 7 (Python 2) on this Ren'Py.
#
# Summertime Saga 0.20 ships only .rpyc files, so its Python can't be fixed at
# the source. Two things cover the differences:
#
# - py3_fixes.json in the game directory maps snippets of the game's Python to
#   versions converted by 2to3 (xrange, unicode, iteritems and so on). It's
#   made on the PC by tools/make_py3_fixes.py.
#
# - Code that Ren'Py 7 compiled as Python 2 (PyCode.py == 2) gets the two
#   Python 2 behaviours that 2to3 can't add: classic division, where
#   int / int rounds down, and Python 2 classes behaving as they did there
#   (see classic_class).

from __future__ import division, absolute_import, print_function

import ast
import builtins
import json

import renpy

FIXES_FILE = "py3_fixes.json"

fixes = None


def load_fixes():
    global fixes

    fixes = {}

    try:
        with renpy.loader.load(FIXES_FILE) as f:
            fixes = json.loads(f.read().decode("utf-8"))
    except Exception:
        pass

    if fixes:
        renpy.display.log.write("Loaded %d Python 3 fixes for the game's scripts.", len(fixes))


def fix_source(source):
    """
    Returns the converted version of `source` if there is one, otherwise
    `source` itself.
    """

    if fixes is None:
        load_fixes()

    if not fixes:
        return source

    return fixes.get(str(source), source)


def classic_div(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return a // b

    return a / b


builtins._classic_div = classic_div


# Python 2 method names, and the Python 3 names that replaced them.
RENAMED_METHODS = (
    ("__nonzero__", "__bool__"),
    ("__div__", "__truediv__"),
    ("__rdiv__", "__rtruediv__"),
    ("__idiv__", "__itruediv__"),
    ("__unicode__", "__str__"),
    )


def classic_class(cls):
    """
    Applied to every class defined by Python 2 code.

    Methods under their Python 2 names get the Python 3 names too, unless
    the class defines those itself. And a class that defines __eq__ without
    __hash__ keeps the hash it inherits: Python 3 makes it unhashable,
    Python 2 didn't.
    """

    d = cls.__dict__

    for old, new in RENAMED_METHODS:
        if old in d and new not in d:
            setattr(cls, new, d[old])

    if "__eq__" in cls.__dict__ and cls.__dict__.get("__hash__", False) is None:
        for base in cls.__mro__[1:]:
            h = base.__dict__.get("__hash__", False)

            if h is not False and h is not None:
                cls.__hash__ = h
                break

    return cls


builtins._classic_class = classic_class


# Built-ins that Python 3 removed. 2to3 rewrites most uses, but not cmp()
# (the credits screen and the safe minigame call it), and code it didn't see
# may use the others. Only names Python 3 doesn't define are added.

import functools


def _cmp(a, b):
    return (a > b) - (a < b)


for _name, _value in (
        ("cmp", _cmp),
        ("unicode", str),
        ("basestring", str),
        ("long", int),
        ("xrange", range),
        ("unichr", chr),
        ("reduce", functools.reduce),
        ):

    if not hasattr(builtins, _name):
        setattr(builtins, _name, _value)


# Standard library names that Python 3 removed.

import time

if not hasattr(time, "clock"):
    time.clock = time.perf_counter

import collections
import collections.abc

for _name in collections.abc.__all__:
    if not hasattr(collections, _name):
        setattr(collections, _name, getattr(collections.abc, _name))

import itertools

for _old, _new in (("izip", zip), ("imap", map), ("ifilter", filter), ("izip_longest", itertools.zip_longest)):
    if not hasattr(itertools, _old):
        setattr(itertools, _old, _new)


class Python2Semantics(ast.NodeTransformer):
    """
    Rewrites a / b into _classic_div(a, b), and a /= b into
    a = _classic_div(a, b). Every class gets the _classic_class decorator.
    """

    def __init__(self, division):
        self.division = division

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        node.decorator_list.append(ast.copy_location(ast.Name(id="_classic_class", ctx=ast.Load()), node))
        return node

    def call(self, left, right, node):
        rv = ast.Call(
            func=ast.Name(id="_classic_div", ctx=ast.Load()),
            args=[left, right],
            keywords=[],
            )

        return ast.copy_location(rv, node)

    def visit_BinOp(self, node):
        self.generic_visit(node)

        if self.division and isinstance(node.op, ast.Div):
            return self.call(node.left, node.right, node)

        return node

    def visit_AugAssign(self, node):
        self.generic_visit(node)

        if not self.division or not isinstance(node.op, ast.Div):
            return node

        if not isinstance(node.target, (ast.Name, ast.Attribute, ast.Subscript)):
            return node

        load = ast.parse(ast.unparse(node.target), mode="eval").body
        ast.copy_location(load, node)

        rv = ast.Assign(targets=[node.target], value=self.call(load, node.value, node))
        return ast.copy_location(rv, node)


def fix_tree(tree, source, py):
    """
    Gives `tree`, compiled from the game's scripts, the Python 2 behaviour
    described at the top of this file. Classic division only applies to code
    compiled as Python 2 (`py` is 2) that didn't ask for true division.
    """

    division = (py == 2) and not ("division" in source and "__future__" in source)

    tree = Python2Semantics(division).visit(tree)
    ast.fix_missing_locations(tree)
    return tree
