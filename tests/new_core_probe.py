"""The patches must stand down on a ComfyUI that already has the fix.

ComfyUI PR #15439 merged upstream on 2026-08-13 and does what both of
this pack's patches do: interior keyframe anchors, and combining
keyframe latents with reference latents instead of letting refs
overwrite them. On such a core, patching anyway would shift positions
core has already placed and rewrite a payload core already built
correctly.

Neither patch may check a version number - a build either carries the
change or it does not, and only behaviour says which. This probe fakes
both eras and requires the patches to detect them.
"""

import os
import sys
import types

import numpy as np

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


def fresh_patch_layout():
    for k in ("patch_layout",):
        sys.modules.pop(k, None)
    import importlib
    return importlib.import_module("patch_layout")


def make_new_core_mm():
    """A PackedLayout that behaves like ComfyUI master after PR #15439.

    Three things changed there and each one can fool a naive probe:
      - `frame_count` is gone from the signature
      - a keyframe only produces cond rows when it carries a latent, and
        the block is sized from that latent's own temporal extent
      - anchors count from a cursor that already includes the refs
    """
    mm = make_mm()
    orig = mm.PackedLayout.__init__

    def merged_init(self, text_len, latent_t, lh, lw, audio_t,
                    keyframes=None, refs=None, **kw):
        stub = [dict(k) for k in (keyframes or [])]
        for k in stub:
            k["resolved_frame_index"] = 0
        orig(self, text_len, latent_t, lh, lw, audio_t,
             keyframes=stub or None, refs=refs)
        conds = [(a, b) for a, b, kind in self.segments if kind == "cond"]
        cursor = float(text_len)
        placed = 0
        for (a, b), k in zip(conds, keyframes or []):
            if k.get("latent") is None:
                continue          # no latent, no rows - as master does
            t = cursor + mm.FRAME_RESCALE * float(
                k.get("resolved_frame_index", 0))
            self.position_ids[a:b, 0] = t
            placed += 1
        # drop any cond segments the caller did not supply a latent for
        if placed < len(conds):
            keep, seen = [], 0
            for a, b, kind in self.segments:
                if kind == "cond":
                    if seen >= placed:
                        seen += 1
                        continue
                    seen += 1
                keep.append((a, b, kind))
            self.segments = keep
        return None

    mm.PackedLayout.__init__ = merged_init
    return mm


def main():
    # --- an OLD core: the patch must install ---
    install(make_mm())
    pl = fresh_patch_layout()
    assert pl.apply_patch() is True, "patch should install on a stock core"
    assert pl.is_applied() is True
    print("1. old core: patch installed, interior anchors enabled")

    # --- a NEW core: the patch must decline ---
    install(make_new_core_mm())
    pl2 = fresh_patch_layout()
    assert pl2._core_handles_interior_anchors() is True, \
        "detection failed to notice core handles interior anchors"
    assert pl2.apply_patch() is False, "patch must NOT install on a fixed core"
    assert pl2.is_applied() is False
    print("2. new core: patch declined and left core alone")

    # --- and core is genuinely untouched afterwards ---
    mmnew = sys.modules["comfy.ldm.minimax.model"]
    before = mmnew.PackedLayout.__init__
    pl2.apply_patch()
    assert mmnew.PackedLayout.__init__ is before, \
        "declining must not replace __init__"
    print("3. new core: PackedLayout.__init__ is still core's own")

    # --- payload: defers when core already concatenated ---
    sys.modules.pop("patch_payload", None)
    mb = types.ModuleType("comfy.model_base")

    class Cond:
        def __init__(self, d):
            self.cond = d

    state = {"combine": False}

    class MiniMaxH3:
        def extra_conds(self, **kw):
            kfs = kw.get("minimax_keyframes") or []
            refs = kw.get("minimax_refs") or []
            kfv = [k["latent"] for k in kfs if "latent" in k]
            rv = [r["latent"] for r in refs if "latent" in r]
            payload = {"cond_video_latents":
                       (kfv + rv) if state["combine"] else rv}
            return {"minimax_payload": Cond(payload)}

    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb
    import importlib
    pp = importlib.import_module("patch_payload")
    assert pp.apply_patch() is True
    inst = MiniMaxH3()
    kf = [{"latent": "K1"}, {"latent": "K2"}]
    rf = [{"latent": "R1"}]

    # old core drops the keyframes; the patch must restore them
    state["combine"] = False
    got = inst.extra_conds(minimax_keyframes=kf, minimax_refs=rf)
    vals = got["minimax_payload"].cond["cond_video_latents"]
    assert vals == ["K1", "K2", "R1"], vals
    print("4. old core: keyframes restored ahead of refs -> %s" % vals)

    # new core already concatenated; the patch must leave it alone.
    # Wrap the ORIGINAL, not the patched method, or the patch calls the
    # wrapper which calls the patch.
    state["combine"] = True
    base = pp._orig_extra_conds

    class Recording(dict):
        writes = []

        def __setitem__(self, k, v):
            Recording.writes.append(k)
            dict.__setitem__(self, k, v)

    def watching(self, **kw):
        out = base(self, **kw)
        rec = Recording()
        dict.update(rec, out["minimax_payload"].cond)
        out["minimax_payload"].cond = rec
        return out

    pp._orig_extra_conds = watching
    got2 = inst.extra_conds(minimax_keyframes=kf, minimax_refs=rf)
    vals2 = dict(got2["minimax_payload"].cond)["cond_video_latents"]
    assert vals2 == ["K1", "K2", "R1"], vals2
    assert Recording.writes == [], \
        "patch rewrote %s on a fixed core" % Recording.writes
    print("5. new core: payload left exactly as core built it, no writes "
          "-> %s" % vals2)

    print("all checks passed")


if __name__ == "__main__":
    main()
