"""Two packs from this lineage must not both patch the layout.

NikoDemon80's Motion Context, this pack, and the forks between them all
lift the same first/last restriction and all smuggle the real frame
index under the same MC_KEY. If two of them own the constructor at once,
both corrections run and every anchor lands twice as far along - silent,
and it looks like a model problem rather than a conflict.

Their pack already stands down when it sees a wrapper named like ours.
This checks the other direction, and checks that standing down does not
make our own node refuse to render.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _mock_harness import make_mm, make_torch  # noqa: E402


def install(mm):
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["torch"] = make_torch()


def fresh():
    sys.modules.pop("patch_layout", None)
    import importlib
    return importlib.import_module("patch_layout")


def main():
    # --- 1. their pack got there first, marked ---
    mm = make_mm()
    install(mm)
    pl = fresh()
    stock = mm.PackedLayout.__init__

    def their_patched_init(self, *a, **kw):
        return stock(self, *a, **kw)
    setattr(their_patched_init, "_h3_motion_context_layout_patch", True)
    mm.PackedLayout.__init__ = their_patched_init

    assert pl.apply_patch() is False, "must not patch over a sibling"
    assert mm.PackedLayout.__init__ is their_patched_init, \
        "their patch was replaced"
    assert pl.is_covered() is True, \
        "standing down must still count as covered, or the node refuses"
    assert pl.is_applied() is False
    print("1. marked sibling: stood down, their patch left in place, "
          "still reported as covered")

    # --- 2. an older unmarked copy, recognised by name ---
    mm2 = make_mm()
    install(mm2)
    pl2 = fresh()
    stock2 = mm2.PackedLayout.__init__

    def _patched_init(self, *a, **kw):       # the shared ancestor's name
        return stock2(self, *a, **kw)
    mm2.PackedLayout.__init__ = _patched_init

    assert pl2.apply_patch() is False, "must not patch over an older copy"
    assert mm2.PackedLayout.__init__ is _patched_init
    assert pl2.is_covered() is True
    print("2. unmarked older copy: recognised by name, stood down")

    # --- 3. nobody else there: we patch, and we mark ourselves ---
    mm3 = make_mm()
    install(mm3)
    pl3 = fresh()
    assert pl3.apply_patch() is True, "should patch a clean core"
    init = mm3.PackedLayout.__init__
    assert getattr(init, pl3.LAYOUT_MARKER, False), \
        "our wrapper must be marked so siblings can recognise it"
    assert getattr(init, "__name__", "") == "_patched_init", \
        "the shared name is what older packs detect us by"
    print("3. clean core: patched, marked, and named so others see us")

    # --- 4. their detection contract, run against our wrapper ---
    # this mirrors their _existing_patch(): marker first, then the name
    def their_check(init):
        if getattr(init, "_h3_motion_context_layout_patch", False):
            return "same"
        if getattr(init, "__name__", "") == "_patched_init":
            return "other"
        return None
    assert their_check(init) == "other", \
        "their pack would not recognise ours and would patch on top"
    print("4. their check sees ours as 'other' -> they stand down too")

    print("all checks passed")


if __name__ == "__main__":
    main()
