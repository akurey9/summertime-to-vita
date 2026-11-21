# Copyright 2026 Akurey
#
# PlayStation Vita support for Ren'Py (Summertime to Vita). MIT license,
# same terms as the rest of Ren'Py.

"""
Memory limits, the left stick cursor and the debug overlay for the Vita
build.

The Vita gives a game around 365MB of main RAM, so the settings a PC game
ships with (Summertime Saga asks for a 512MB image cache) have to be
overridden. apply_limits() runs after the game's init code, so it wins over
anything set in the game's config. check_memory() is called at the start of
each interaction and on scene changes, and frees memory once the heap gets
close to full.

Defaults can be changed for testing with environment variables:

    RENPY_VITA_IMAGE_CACHE_MB     image cache size (default 96)
    RENPY_VITA_HEAP_SOFT_LIMIT    fraction of the heap that triggers a
                                  cleanup (default 0.80)
"""

from __future__ import division, absolute_import, print_function

import gc
import os
import time

import renpy


def _env_number(name, default, kind):
    try:
        return kind(os.environ.get(name, default))
    except ValueError:
        return default


IMAGE_CACHE_MB = _env_number("RENPY_VITA_IMAGE_CACHE_MB", 96, int)
HEAP_SOFT_LIMIT = _env_number("RENPY_VITA_HEAP_SOFT_LIMIT", 0.80, float)
HEAP_HARD_LIMIT = _env_number("RENPY_VITA_HEAP_HARD_LIMIT", 0.90, float)

# Minimum seconds between collections, and between last-resort cleanups.
COLLECT_INTERVAL = 10.0
FREE_MEMORY_INTERVAL = 30.0

last_collect = 0.0
last_free_memory = 0.0

PREDICT_STATEMENTS = 4

# Audio buffer, in samples. Ren'Py's default (2048, about 40ms) runs dry
# whenever the CPU is busy for longer than that, which on the Vita is often.
SOUND_BUFFER_SIZE = 4096
SCREEN_CACHE_SIZE = 2

# Don't check more than once a second; mallinfo() walks the heap.
CHECK_INTERVAL = 1.0

last_check = 0.0

# Number of times each cleanup level ran, shown by the debug overlay.
stats = {"collect": 0, "free_memory": 0}


def memory_info():
    """
    Returns renpy.renpyvita.memory_info(), or None when it isn't available.
    """

    try:
        import renpy.renpyvita
    except ImportError:
        return None

    return renpy.renpyvita.memory_info()


def apply_limits():
    """
    Clamps the game's cache and prediction settings to what fits on the
    Vita. Called just before the image cache is set up.
    """

    config = renpy.config

    config.image_cache_size = None
    config.image_cache_size_mb = min(config.image_cache_size_mb, IMAGE_CACHE_MB)

    config.predict_statements = min(config.predict_statements, PREDICT_STATEMENTS)
    config.predict_screens = False
    config.screen_cache_size = min(config.screen_cache_size, SCREEN_CACHE_SIZE)

    # Keep only the GPU copy of each image once it's uploaded.
    config.cache_surfaces = False

    if config.sound_buffer_size is None:
        config.sound_buffer_size = SOUND_BUFFER_SIZE

    # The art is prepared at the Vita's own resolution and drawn 1:1, so
    # mipmaps would only add a third to every texture. ("auto" turns them
    # on here, since the screen is half the 1920x1080 layout.)
    config.mipmap = False

    renpy.display.log.write(
        "Vita limits: image cache %dMB, predict %d statements, screen cache %d.",
        config.image_cache_size_mb, config.predict_statements, config.screen_cache_size)


def over_limit(info, limit=None):
    if not info or not info["heap_size"]:
        return False

    if limit is None:
        limit = HEAP_SOFT_LIMIT

    return info["heap_used"] > info["heap_size"] * limit


def log_heap(when):
    info = memory_info()

    if info:
        renpy.display.log.write("Heap %s: %.1f of %.1f MB used.", when,
            info["heap_used"] / 1048576.0, info["heap_size"] / 1048576.0)


def freeze_objects():
    """
    Moves every object alive now out of the garbage collector's reach. The
    game keeps over a million long-lived objects (its script, images and
    state), and a full collection walking all of them takes seconds on the
    Vita, which showed up as random freezes. Called after startup and after
    each script file loaded on demand.

    Garbage is collected first: frozen objects are never freed, so freezing
    uncollected garbage would leak it. Only objects created since the last
    freeze are walked, so this is quick after the first time.
    """

    gc.collect()
    gc.freeze()


def drop_startup_caches():
    """
    Ren'Py keeps the Python syntax trees it built to analyze the screens,
    though they're only needed at startup (they're saved to
    cache/py3analysis.rpyb, and rebuilt if something asks again). For a
    large game that's tens of megabytes of heap.
    """

    pa = renpy.pyanalysis
    pa.ccache = pa.CompilerCache()
    pa.new_ccache = pa.CompilerCache()

    gc.collect()

    freeze_objects()

    log_heap("after startup")


def check_memory(force=False):
    """
    Keeps the heap in check. Above the soft limit, runs a garbage collection
    (cheap, since the long-lived objects are frozen), at most every
    COLLECT_INTERVAL seconds. Only above the hard limit, and at most once a
    minute, does it call renpy.free_memory(): that mostly drops textures,
    which live outside the heap, and reloading everything on screen afterwards
    is a long freeze.
    """

    global last_check, last_collect, last_free_memory

    now = time.time()

    if not force and (now - last_check) < CHECK_INTERVAL:
        return

    last_check = now

    info = memory_info()

    if not over_limit(info):
        return

    if force or (now - last_collect) >= COLLECT_INTERVAL:
        last_collect = now
        gc.collect()
        stats["collect"] += 1
        info = memory_info()

    if not over_limit(info, HEAP_HARD_LIMIT):
        return

    if not force and (now - last_free_memory) < FREE_MEMORY_INTERVAL:
        return

    last_free_memory = now

    renpy.display.log.write(
        "Vita heap at %d/%d bytes, freeing memory.", info["heap_used"], info["heap_size"])

    reclaim_memory()
    stats["free_memory"] += 1


def reclaim_memory():
    """
    Gets back as much of the heap as possible: Ren'Py's caches, the layered
    images not on screen (they're rebuilt when next shown), and a full
    garbage collection including the frozen objects. Objects frozen while
    they were alive and that died later (old displayables, rollback entries)
    are only reclaimed this way. Takes a few seconds, so it's kept for when
    the heap is nearly full.
    """

    before = memory_info()
    start = time.time()

    try:
        renpy.exports.free_memory()
    except Exception as e:
        renpy.display.log.write("free_memory failed: %r", e)

    try:
        renpy.store.LazyLayeredImage.drop_all()
    except Exception as e:
        renpy.display.log.write("Dropping layered images failed: %r", e)

    gc.unfreeze()
    gc.collect()
    gc.freeze()

    after = memory_info()

    if before and after:
        renpy.display.log.write("Reclaimed %.1f MB of heap in %.1f s (%.1f of %.1f MB used).",
            (before["heap_used"] - after["heap_used"]) / 1048576.0, time.time() - start,
            after["heap_used"] / 1048576.0, after["heap_size"] / 1048576.0)


################################################################################
# Left stick cursor
################################################################################

# The front touch screen is the main input, as on Android. The left stick
# also moves a pointer, for anything that's easier to aim than to tap; while
# it's in use, Cross clicks where the pointer is. Touching the screen or
# using the D-pad hands control back to touch and focus navigation.

# The SDL mouse id for events synthesized from touch (SDL_TOUCH_MOUSEID).
TOUCH_MOUSE_ID = 4294967295

STICK_DEADZONE = 7000
STICK_MAX = 32767.0

# Screen widths per second at full deflection.
CURSOR_SPEED = 1.0

# Moving the SDL pointer makes Ren'Py send a mouse motion event through every
# displayable on screen to update hover and focus, which costs more than a
# frame on busy screens. The drawn pointer follows the stick every tick, but
# the SDL pointer only at most this often, and once more when it stops.
CURSOR_WARP_INTERVAL = 0.1
cursor_last_warp = 0.0
cursor_warped = True

# Hide the pointer after this long without stick movement.
CURSOR_IDLE_HIDE = 6.0

stick = {"leftx": 0, "lefty": 0}

cursor_active = False
cursor_x = 960.0
cursor_y = 540.0
cursor_last_move = 0.0
cursor_last_tick = None

# Set while Cross is held as a mouse button, so the release is sent as a
# mouse release too.
cursor_button_down = False

DPAD = ("dpup", "dpdown", "dpleft", "dpright")


class OverlayDisplayable(renpy.display.displayable.Displayable):
    """
    Drawn above everything else (as config.mouse_displayable): the stick
    pointer and, when it's switched on, the debug overlay.
    """

    SIZE = 36
    THICK = 4
    OUTLINE = 2

    def render(self, width, height, st, at):
        rv = renpy.display.render.Render(width, height)

        if hud_visible:
            hud = renpy.display.render.render(hud_text(), width, height, st, at)
            hw, hh = hud.get_size()

            bg = renpy.display.render.render(
                renpy.display.imagelike.Solid("#000b"), hw + 24, hh + 16, st, at)

            rv.blit(bg, (8, 8))
            rv.blit(hud, (20, 16))

            renpy.display.render.redraw(self, HUD_REFRESH)

        if cursor_active:
            self.render_cursor(rv, st, at)

        return rv

    def render_cursor(self, rv, st, at):
        s = self.SIZE
        t = self.THICK
        o = self.OUTLINE

        x = int(cursor_x)
        y = int(cursor_y)

        def bar(w, h, color):
            return renpy.display.render.render(
                renpy.display.imagelike.Solid(color), w, h, st, at)

        # Outline, then the white cross on top.
        rv.blit(bar(s + 2 * o, t + 2 * o, "#000a"), (x - s // 2 - o, y - t // 2 - o))
        rv.blit(bar(t + 2 * o, s + 2 * o, "#000a"), (x - t // 2 - o, y - s // 2 - o))
        rv.blit(bar(s, t, "#fff"), (x - s // 2, y - t // 2))
        rv.blit(bar(t, s, "#fff"), (x - t // 2, y - s // 2))


overlay_displayable = OverlayDisplayable()


def setup_input():
    """
    Called after the game's init code. Draws the stick pointer and the
    debug overlay, unless the game supplies its own mouse displayable.
    """

    if renpy.config.mouse_displayable is None:
        renpy.config.mouse_displayable = overlay_displayable


def _redraw():
    renpy.display.render.redraw(overlay_displayable, 0)


def set_cursor_active(active):
    global cursor_active, cursor_last_tick, cursor_x, cursor_y

    if active == cursor_active:
        return

    cursor_active = active
    cursor_last_tick = None

    if active:
        # Start from wherever the pointer (or the last touch) is.
        try:
            cursor_x, cursor_y = renpy.display.draw.get_mouse_pos()
        except Exception:
            pass

    _redraw()


def _post_mouse_button(event_type):
    pygame = renpy.pygame
    pos = renpy.display.draw.untranslate_point(cursor_x, cursor_y)
    pos = (int(pos[0]), int(pos[1]))

    pygame.event.post(pygame.event.Event(event_type, {"pos": pos, "button": 1, "which": 0, "touch": False}))


def controller_axis(axis, value):
    """
    Called with each left stick axis motion. Returns True if the cursor
    handled it, so it isn't also turned into focus movement.
    """

    if axis not in stick:
        return False

    stick[axis] = value

    if abs(value) > STICK_DEADZONE:
        set_cursor_active(True)

    return True


def controller_button(button, pressed):
    """
    Called with each controller button. Returns True if the cursor took the
    button (Cross while the pointer is showing).
    """

    global cursor_button_down

    pygame = renpy.pygame

    if hud_combo(button, pressed):
        return True

    if button in DPAD and pressed:
        set_cursor_active(False)
        return False

    if button != "a":
        return False

    if pressed and cursor_active:
        if not cursor_warped:
            _warp_cursor(time.time())

        cursor_button_down = True
        _post_mouse_button(pygame.MOUSEBUTTONDOWN)
        return True

    if not pressed and cursor_button_down:
        cursor_button_down = False
        _post_mouse_button(pygame.MOUSEBUTTONUP)
        return True

    return False


def mouse_event(ev):
    """
    Watches mouse events: a touch on the front screen hides the pointer.
    """

    pygame = renpy.pygame

    if ev.type == pygame.MOUSEBUTTONDOWN and getattr(ev, "which", 0) == TOUCH_MOUSE_ID:
        set_cursor_active(False)


def cursor_periodic():
    """
    Moves the pointer by the stick position. Called from the controller's
    periodic handler (every PERIODIC tick, about 20 times a second).
    """

    global cursor_x, cursor_y, cursor_last_tick, cursor_last_move
    global cursor_last_warp, cursor_warped

    if not cursor_active:
        return

    now = time.time()

    if cursor_last_tick is None:
        cursor_last_tick = now
        cursor_last_move = now
        return

    dt = min(now - cursor_last_tick, 0.1)
    cursor_last_tick = now

    w = renpy.config.screen_width
    h = renpy.config.screen_height

    dx = _stick_speed(stick["leftx"]) * w
    dy = _stick_speed(stick["lefty"]) * w

    if not (dx or dy):
        # The stick came to rest: put the SDL pointer where the drawn one is.
        if not cursor_warped:
            _warp_cursor(now)

        if now - cursor_last_move > CURSOR_IDLE_HIDE and not cursor_button_down:
            set_cursor_active(False)
        return

    cursor_last_move = now

    cursor_x = min(max(cursor_x + dx * dt, 0), w - 1)
    cursor_y = min(max(cursor_y + dy * dt, 0), h - 1)

    cursor_warped = False

    if now - cursor_last_warp >= CURSOR_WARP_INTERVAL:
        _warp_cursor(now)

    _redraw()


def _warp_cursor(now):
    """
    Moves the SDL pointer to the drawn one. That also sends a mouse motion
    event, so hover and focus update as they would for a real mouse.
    """

    global cursor_last_warp, cursor_warped

    cursor_last_warp = now
    cursor_warped = True
    renpy.display.draw.set_mouse_pos(cursor_x, cursor_y)


def _stick_speed(value):
    """
    Converts a stick axis value into pointer speed. Squared, so small
    movements are precise and full deflection is fast.
    """

    if abs(value) <= STICK_DEADZONE:
        return 0.0

    f = (abs(value) - STICK_DEADZONE) / (STICK_MAX - STICK_DEADZONE)
    f = min(f, 1.0)

    speed = f * f * CURSOR_SPEED

    return speed if value > 0 else -speed


################################################################################
# Debug overlay
################################################################################

# Hold Select and press Start to show or hide it. It reports what matters
# for tuning on hardware: frame rate, heap use, what the kernel has left,
# and the image cache.

HUD_REFRESH = 0.5

hud_visible = False

# Select is held, and whether Start was pressed during this hold (then the
# Select release mustn't also open the inventory).
select_down = False
combo_used = False


def toggle_hud():
    global hud_visible

    hud_visible = not hud_visible
    _redraw()


def hud_combo(button, pressed):
    """
    Watches Select and Start. Returns True for the presses and releases
    that make up a Select+Start, so they don't also act as their own
    buttons.
    """

    global select_down, combo_used

    if button == "back":
        if pressed:
            select_down = True
            combo_used = False
            return False

        select_down = False

        if combo_used:
            combo_used = False
            return True

        return False

    if button == "start" and select_down:
        if pressed:
            combo_used = True
            toggle_hud()

        return True

    return False


################################################################################
# Profiler
################################################################################

# A sampling profiler: a background thread looks at the main thread's Python
# stack SAMPLE_INTERVAL times a second and counts where it is. Unlike
# cProfile (which in Python 3.12 hooks every call on every thread and slows
# this game down a lot), it costs about a percent, so the game runs at its
# real speed while it's measured. profile.txt lists the functions the main
# thread was in most: on its own ("self"), and anywhere on the stack
# ("total"). Waiting for input counts as idle and is left out.

SAMPLE_INTERVAL = 0.02

# How often the report is written out. The game can be closed at any moment,
# so it's kept up to date as it runs.
PROFILE_WRITE_SECONDS = 60.0

profiler = None
profile_started = 0.0

# Functions that mean the main thread is waiting, not working.
IDLE_FUNCTIONS = {"event_wait", "idle_frame", "sleep", "wait"}


class Sampler(object):

    def __init__(self):
        self.self_counts = {}
        self.total_counts = {}
        self.samples = 0
        self.idle = 0
        self.running = True

    def sample(self, frame):
        import sys

        leaf = frame.f_code

        if leaf.co_name in IDLE_FUNCTIONS:
            self.idle += 1
            return

        self.samples += 1

        key = "%s:%d(%s)" % (leaf.co_filename.rpartition("/")[2], frame.f_lineno, leaf.co_name)
        self.self_counts[key] = self.self_counts.get(key, 0) + 1

        seen = set()
        f = frame

        while f is not None:
            code = f.f_code

            if code not in seen:
                seen.add(code)
                key = "%s:%d(%s)" % (code.co_filename.rpartition("/")[2], code.co_firstlineno, code.co_name)
                self.total_counts[key] = self.total_counts.get(key, 0) + 1

            f = f.f_back

    def run(self):
        import sys

        last_write = time.time()

        while self.running:
            time.sleep(SAMPLE_INTERVAL)

            frame = sys._current_frames().get(main_thread_ident)

            if frame is not None:
                try:
                    self.sample(frame)
                except Exception:
                    pass

            del frame

            now = time.time()

            if now - last_write >= PROFILE_WRITE_SECONDS:
                last_write = now
                self.write()

    def write(self):
        seconds = time.time() - profile_started
        busy = self.samples or 1
        lines = [ ]

        lines.append("Profile of %.1f seconds, %d frames drawn. Main thread busy %.0f%% of the time (%d samples, %d idle)." % (
            seconds, frame_count, 100.0 * self.samples / max(1, self.samples + self.idle), self.samples, self.idle))
        lines.append("Percentages are of the busy time.")
        lines.append("")

        for title, counts in (("By own time", self.self_counts), ("Including callees", self.total_counts)):
            lines.append("=== %s ===" % title)

            for key, n in sorted(counts.items(), key=lambda i: -i[1])[:60]:
                lines.append("%6.1f%%  %6d  %s" % (100.0 * n / busy, n, key))

            lines.append("")

        path = os.path.join(renpy.config.basedir, "profile.txt")

        try:
            with open(path, "w") as f:
                f.write("\n".join(lines))
        except Exception:
            pass


def start_profile():
    """
    Starts the sampling profiler.
    """

    global profiler, profile_started

    if profiler is not None:
        return

    import threading

    profiler = Sampler()
    profile_started = time.time()

    t = threading.Thread(target=profiler.run, name="profiler", daemon=True)
    t.start()

    renpy.display.log.write("Profiler started; profile.txt is written every %d seconds.", PROFILE_WRITE_SECONDS)

    import atexit
    atexit.register(stop_profile)


def stop_profile():
    """
    Stops the profiler and writes the final report.
    """

    global profiler

    if profiler is None:
        return

    profiler.running = False
    profiler.write()
    profiler = None


def _mb(n):
    return n / 1048576.0


def fps():
    """
    Frames actually drawn per second over Ren'Py's performance window.
    Ren'Py only redraws when something changes, so a still screen reads low.
    """

    interface = renpy.display.interface

    if interface is None:
        return 0.0

    times = interface.frame_times

    if len(times) < 2:
        return 0.0

    span = times[-1] - times[0]

    if span <= 0:
        return 0.0

    return (len(times) - 1) / span


def hud_lines():
    lines = ["%.1f fps" % fps()]

    info = memory_info()

    if info:
        if info["heap_size"]:
            lines.append("Heap %.1f / %.0f MB (%d%%)" % (
                _mb(info["heap_used"]), _mb(info["heap_size"]),
                100.0 * info["heap_used"] / info["heap_size"]))
        else:
            lines.append("Heap %.1f MB" % _mb(info["heap_used"]))

        lines.append("Free RAM %.1f MB   CDRAM %.1f MB   Phys %.1f MB" % (
            _mb(info["free_user"]), _mb(info["free_cdram"]), _mb(info["free_phycont"])))

    cache = renpy.display.im.cache

    # The cache counts pixels; 4 bytes each.
    lines.append("Image cache %.1f / %.0f MB" % (
        _mb(cache.get_total_size() * 4), _mb(cache.cache_limit * 4)))

    lines.append("Cleanups: gc %d, free_memory %d" % (stats["collect"], stats["free_memory"]))

    return "\n".join(lines)


def hud_text():
    return renpy.text.text.Text(
        hud_lines(),
        size=24,
        color="#fff",
        outlines=[ (2, "#000", 0, 0) ],
        substitute=False,
        )


################################################################################
# Game file check
################################################################################

# The game's files are copied to the memory card by hand, and a copy that
# stopped early or got damaged on the way makes Ren'Py fail while reading the
# archive indexes, with nothing on screen that says why. tools/
# prepare_vita_assets.py writes vita_files.txt with the size of every archive
# and a SHA-1 of its index; this compares against it before the loader runs.

GAME_FILES_LIST = "vita_files.txt"


class GameFilesError(Exception):
    pass


def _rpa_index_region(f):
    """
    Returns (offset, index_bytes) for an open RPA-3.0 archive.
    """

    header = f.read(40)

    if not header.startswith(b"RPA-3.0 "):
        raise ValueError("not a RPA-3.0 archive (header %r)" % header[:16])

    offset = int(header[8:24], 16)
    f.seek(offset)

    return offset, f.read()


def _read_at_sequential(path, offset, count):
    """
    Reads `count` bytes at `offset` without seeking, to tell a seek problem
    apart from bad data.
    """

    with open(path, "rb") as f:
        remaining = offset

        while remaining > 0:
            chunk = f.read(min(remaining, 1 << 20))

            if not chunk:
                return b""

            remaining -= len(chunk)

        return f.read(count)


def check_game_files():
    import hashlib
    import zlib

    gamedir = renpy.config.gamedir
    log = renpy.display.log

    expected = {}

    try:
        with open(os.path.join(gamedir, GAME_FILES_LIST), "r") as f:
            for line in f:
                parts = line.split()

                if len(parts) == 3:
                    expected[parts[0]] = (int(parts[1]), parts[2])

    except OSError:
        log.write("%s not found in %s; only checking that archives open.", GAME_FILES_LIST, gamedir)

    try:
        names = sorted(i for i in os.listdir(gamedir) if i.lower().endswith(".rpa"))
    except OSError as e:
        raise GameFilesError("Can't read the game folder %s (%s). Copy the game files there." % (gamedir, e))

    if not names and not expected:
        if not os.path.isdir(os.path.join(gamedir, "scripts")):
            raise GameFilesError("No game files in %s. Copy the game files there." % gamedir)

        log.write("No archives in %s; the game files are loose.", gamedir)
        return

    problems = []

    for name in sorted(set(names) | set(expected)):
        path = os.path.join(gamedir, name)

        try:
            size = os.path.getsize(path)
        except OSError:
            problems.append("%s is missing" % name)
            continue

        want_size, want_sha1 = expected.get(name, (None, None))

        if want_size is not None and size != want_size:
            problems.append("%s is %d bytes, should be %d (incomplete copy?)" % (name, size, want_size))
            continue

        try:
            with open(path, "rb") as f:
                offset, data = _rpa_index_region(f)

            sha1 = hashlib.sha1(data).hexdigest()

            if want_sha1 is not None and sha1 != want_sha1:
                # Same size but different index bytes: either the file is
                # damaged or reading it at an offset doesn't work here.
                direct = _read_at_sequential(path, offset, 16)
                problems.append(
                    "%s: index doesn't match (read with seek: %s, read in order: %s)" % (
                        name, data[:16].hex(), direct.hex()))
                continue

            zlib.decompress(data)

        except Exception as e:
            problems.append("%s can't be read: %s" % (name, e))
            continue

        log.write("Game file OK: %s (%d bytes)", name, size)

    if problems:
        for i in problems:
            log.write("Game file problem: %s", i)
            print("Game file problem:", i)

        raise GameFilesError(
            "Some game files in %s are damaged or incomplete:\n\n%s\n\n"
            "Copy them to the Vita again." % (gamedir, "\n".join(problems)))


################################################################################
# Oversampled images
################################################################################

# The Vita build ships the art at half size and draws it with oversample=0.5
# (the game's own art manifest sets that up). Stock Ren'Py turns such an image
# into a Render that holds the texture behind a scaling matrix. Anything that
# then crops that render - Frame's nine-slice borders, bar ends, crop
# transforms - goes through Render.subsurface's matrix path and picks the
# wrong region, so frames come out pinched and crops lose their bottom edge.
#
# On GL2 the texture can be scaled into a model of the image's full size
# instead (GL2Model.scale). Crops of that are exact (GL2Model.subsurface
# cuts the mesh), and nothing else about drawing changes.


def _make_render(ce):
    Render = renpy.display.render.Render

    bounds = ce.bounds[:2]
    oversample = ce.what.get_oversample() or 0.001
    texture = ce.texture

    if oversample != 1:
        inv_oversample = 1.0 / oversample

        rv = Render(ce.width * inv_oversample, ce.height * inv_oversample)
        bounds = tuple(round(el / oversample) for el in bounds)

        scale = getattr(texture, "scale", None)

        if scale is not None:
            model = scale(inv_oversample)

            # The copy doesn't carry the texture itself; GLTexture.subsurface
            # re-attaches it the same way.
            model.set_texture(0, texture)

            rv.blit(model, bounds)
        else:
            # Software renderer: keep Ren'Py's own approach.
            Matrix2D = renpy.display.matrix.Matrix2D
            rv.forward = Matrix2D(oversample, 0, 0, oversample)
            rv.reverse = Matrix2D(inv_oversample, 0, 0, inv_oversample)
            rv.blit(texture, bounds)

    else:
        rv = Render(ce.width, ce.height)
        rv.blit(texture, bounds)

    if ce.what.pixel_perfect:
        rv.add_property("pixel_perfect", True)

    # cached_texture lets shaders sample the image's texture directly. For a
    # half-size image that texture is half the image's size, and the game's
    # shaders measure in texture pixels (its Frame's u_border is 30px of a
    # 72px-wide button), so they'd come out distorted. Without it, Ren'Py
    # renders the image to a texture of its real size first.
    if oversample == 1 and ce.bounds == (0, 0, ce.width, ce.height):
        rv.cached_texture = texture

    return rv


# Folders whose art tools/prepare_vita_assets.py keeps at full size (menus
# and minigames), from vita_fullres.txt in the game folder: "+prefix" to
# include, "-prefix" to exclude. The game gives every image oversample=0.5,
# so these are switched back to 1.
FULL_RES_LIST = "vita_fullres.txt"

full_res_include = ()
full_res_exclude = ()


def load_full_res_list():
    global full_res_include, full_res_exclude

    include = []
    exclude = []

    try:
        with open(os.path.join(renpy.config.gamedir, FULL_RES_LIST), "r") as f:
            for line in f:
                line = line.strip()

                if line.startswith("+"):
                    include.append(line[1:])
                elif line.startswith("-"):
                    exclude.append(line[1:])

    except OSError:
        renpy.display.log.write("%s not found; drawing all art at the manifest scale.", FULL_RES_LIST)

    full_res_include = tuple(include)
    full_res_exclude = tuple(exclude)


def is_full_res(filename):
    return filename.startswith(full_res_include) and not filename.startswith(full_res_exclude)


def install_render_fix():
    renpy.display.im.Cache._make_render = staticmethod(_make_render)

    load_full_res_list()

    if not full_res_include:
        return

    Image = renpy.display.im.Image
    get_oversample = Image.get_oversample

    def vita_get_oversample(self):
        if is_full_res(self.filename):
            return 1

        return get_oversample(self)

    Image.get_oversample = vita_get_oversample


################################################################################
# Freeze diagnostics
################################################################################

# How long the screen can go without a frame before the main thread's Python
# stack is written to log.txt, and before the native watchdog crashes the game
# on purpose to get a core dump (only during the first minutes of play).
FREEZE_LOG_SECONDS = _env_number("RENPY_VITA_FREEZE_LOG", 8, int)
FREEZE_DUMP_SECONDS = _env_number("RENPY_VITA_FREEZE_DUMP", 0, int)
FREEZE_DUMP_WINDOW = _env_number("RENPY_VITA_FREEZE_WINDOW", 300, int)

# Profile the main thread for the whole session (see start_profile).
PROFILE_SESSION = _env_number("RENPY_VITA_PROFILE", 1, int)

# The first few seconds of play get a status line every couple of seconds.
STATUS_SECONDS = 60

frame_count = 0
last_frame_time = 0.0
first_frame_time = 0.0
main_thread_ident = None

# Seconds spent in Interface.draw_screen, and image decodes (count, seconds).
draw_seconds = 0.0
decode_count = 0
decode_seconds = 0.0

# Every this many seconds, a line in log.txt sums up what the game did.
PERF_SECONDS = 10.0


def _perf_line(prev):
    """
    Returns (line, totals): what happened since `prev` (the totals from the
    last call).
    """

    try:
        import renpy.gl2.gl2texture as gt
        tex = list(gt.vita_counters)
    except Exception:
        tex = [ 0, 0.0, 0, 0.0 ]

    totals = (time.time(), frame_count, draw_seconds, decode_count, decode_seconds) + tuple(tex)

    if prev is None:
        return None, totals

    d = [ a - b for a, b in zip(totals, prev) ]
    seconds = d[0] or 1.0
    frames = d[1]

    info = memory_info()

    if info:
        heap = "%.0f/%.0f MB, free RAM %.0f MB, free CDRAM %.0f MB" % (
            info["heap_used"] / 1048576.0, info["heap_size"] / 1048576.0,
            info["free_user"] / 1048576.0, info["free_cdram"] / 1048576.0)
    else:
        heap = "?"

    try:
        layered = sum(renpy.store.LazyLayeredImage.build_counts.values())
    except Exception:
        layered = 0

    import threading

    line = ("[perf] %.1f fps, draw %d ms/frame, decode %d images %d ms, upload %d textures %d ms, "
        "render-to-texture %d %d ms, heap %s, gc %s, layered images built %d, threads %d. %s") % (
        frames / seconds,
        (d[2] * 1000.0 / frames) if frames else 0,
        d[3], d[4] * 1000.0,
        d[5], d[6] * 1000.0,
        d[7], d[8] * 1000.0,
        heap,
        gc.get_count(),
        layered,
        threading.active_count(),
        _status_line(),
        )

    return line, totals


def _status_line():
    try:
        context = renpy.game.context()
        node = context.current
        screens = [ ]

        for layer in ("screens", "overlay"):
            for sle in context.scene_lists.layers.get(layer, ()):
                name = getattr(sle.displayable, "screen_name", None)
                if name:
                    screens.append(" ".join(name))

        return "node %r, screens %s" % (node, ", ".join(screens) or "none")

    except Exception as e:
        return "status unavailable (%r)" % (e,)


def _log_main_stack(idle):
    import sys
    import traceback

    frame = sys._current_frames().get(main_thread_ident)

    lines = [ "No frame for %.1f seconds after %d frames. %s" % (idle, frame_count, _status_line()) ]

    if frame is not None:
        lines.append("Main thread is at:")
        lines.extend(l.rstrip("\n") for l in traceback.format_stack(frame))
    else:
        lines.append("Main thread stack not available.")

    # One write: every write to log.txt is flushed to the memory card.
    renpy.display.log.write("%s", "\n".join(lines))

    try:
        with open(os.path.join(renpy.config.basedir, "freeze.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _freeze_watch():
    reported = None
    last_status = 0.0
    last_perf = 0.0
    perf_totals = None

    while True:
        time.sleep(1.0)

        now = time.time()

        if not last_frame_time:
            continue

        if now - last_perf >= PERF_SECONDS:
            last_perf = now
            line, perf_totals = _perf_line(perf_totals)

            if line:
                renpy.display.log.write("%s", line)

        idle = now - last_frame_time

        if idle >= FREEZE_LOG_SECONDS and reported != last_frame_time:
            reported = last_frame_time
            _log_main_stack(idle)


def install_freeze_watch():
    """
    Counts drawn frames and starts the watchers above.
    """

    import threading

    global main_thread_ident
    main_thread_ident = threading.get_ident()

    # A timed wait has to actually wait, or every background thread spins.
    lock = threading.Lock()
    lock.acquire()
    start = time.perf_counter()
    lock.acquire(timeout=0.05)
    renpy.display.log.write("Timed wait check: a 50 ms wait took %.1f ms.", (time.perf_counter() - start) * 1000.0)

    try:
        import renpy.renpyvita as native
    except ImportError:
        native = None

    Interface = renpy.display.core.Interface
    draw_screen = Interface.draw_screen

    def vita_draw_screen(self, root_widget, fullscreen_video, draw):
        global frame_count, last_frame_time, first_frame_time, draw_seconds

        start = time.time()

        rv = draw_screen(self, root_widget, fullscreen_video, draw)

        frame_count += 1
        last_frame_time = time.time()
        draw_seconds += last_frame_time - start

        if native is not None:
            native.heartbeat()

        if not first_frame_time:
            first_frame_time = last_frame_time

            # The whole session is profiled, from the first frame.
            # A file named noprofile.txt next to the game files turns it off.
            if PROFILE_SESSION and not os.path.exists(os.path.join(renpy.config.basedir, "noprofile.txt")):
                start_profile()

            if native is not None and FREEZE_DUMP_SECONDS > 0:
                native.watchdog_start(FREEZE_DUMP_SECONDS, FREEZE_DUMP_WINDOW,
                    os.path.join(renpy.config.basedir, "freeze-native.txt"))

        return rv

    Interface.draw_screen = vita_draw_screen

    load_image = renpy.display.pgrender.load_image

    def vita_load_image(*args, **kwargs):
        global decode_count, decode_seconds

        start = time.time()

        try:
            return load_image(*args, **kwargs)
        finally:
            dt = time.time() - start
            decode_count += 1
            decode_seconds += dt

            if dt > 0.15:
                import threading
                renpy.display.log.write("Slow image load: %s took %d ms (%s thread).",
                    args[1] if len(args) > 1 else "?", dt * 1000,
                    "main" if threading.get_ident() == main_thread_ident else "background")

    renpy.display.pgrender.load_image = vita_load_image

    t = threading.Thread(target=_freeze_watch, name="vita_freeze_watch")
    t.daemon = True
    t.start()
