# Summertime to Vita - PS Vita button layout.
#
# Touch works as on the Android release. The buttons add:
#
#   Cross       confirm / advance (clicks under the pointer while the left
#               stick pointer is showing)
#   Circle      closes the phone or backpack; opens the phone while walking
#               around; otherwise the pause menu, which also closes menus
#   Triangle    town map while walking around; otherwise hides the text box
#   Square      shows or hides the quick menu
#   Select      backpack while walking around
#   Select+Start  hold Select, press Start: debug overlay
#   Start       pause menu
#   L / R       rollback / skip
#   D-pad       moves between buttons and hotspots
#   Left stick  moves a pointer

init 900 python:

    if renpy.variant("vita"):

        config.pad_bindings.update({
            "pad_b_press" : [ "vita_circle", "game_menu" ],
            "pad_y_press" : [ "vita_triangle", "hide_windows" ],
            "pad_x_press" : [ "vita_square" ],
            # On release, so holding Select for Select+Start (debug overlay)
            # doesn't open the backpack.
            "pad_back_press" : [ ],
            "pad_back_release" : [ "vita_select" ],
            "pad_rightshoulder_press" : [ "toggle_skip" ],
            })

        for _vita_name in ("repeat_pad_back_press", "repeat_pad_rightshoulder_press"):
            config.pad_bindings.pop(_vita_name, None)

        config.overlay_screens.append("vita_pad")

    def vita_walking():
        """
        True while the player is looking at a location with the HUD up,
        and not in dialogue or a menu.
        """

        return (renpy.get_screen("ui") is not None
            and renpy.get_screen("say") is None
            and renpy.get_screen("choice") is None)

    def vita_map_unlocked():
        return bool(getattr(store.game, "ui_locked", True))

    def vita_quick_menu():
        store.quick_menu = not getattr(store, "quick_menu", True)
        renpy.restart_interaction()


# The same actions as the HUD buttons (core/user_interface), for whatever
# is on screen. Anything not caught here falls through to the bindings after
# it (the pause menu for Circle, hiding the text box for Triangle).
screen vita_pad():

    if not _menu and renpy.get_mode() == "screen":

        if renpy.get_screen("phone"):
            key "vita_circle" action Hide("phone")

        elif renpy.get_screen("backpack"):
            key "vita_circle" action OpenBackpack()
            key "vita_select" action OpenBackpack()

        elif vita_walking():
            key "vita_circle" action Show("phone")
            key "vita_select" action OpenBackpack()

            if vita_map_unlocked():
                key "vita_triangle" action MapAction()

    key "vita_square" action Function(vita_quick_menu)
