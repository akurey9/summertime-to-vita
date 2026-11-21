# Stand-in for renpy.gl2.assimp on platforms built without assimp (the
# PlayStation Vita). Keeps the hooks the rest of Ren'Py calls, and makes
# GLTFModel fail with a clear message if a game tries to use it.

from __future__ import division, absolute_import, print_function

import threading

from renpy.display.displayable import Displayable

loader = None
loader_lock = threading.Lock()


def free_memory():
    return


def finish_predict():
    return


def preload():
    return


class GLTFModel(Displayable):

    def __init__(self, *args, **kwargs):
        raise Exception("GLTFModel is not supported on this platform.")
