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

    # --- a NEW core: video stands down, audio placement stays ours ---
    install(make_new_core_mm())
    pl2 = fresh_patch_layout()
    assert pl2._core_handles_interior_anchors() is True, \
        "detection failed to notice core handles interior anchors"
    assert pl2.apply_patch() is True, \
        "patch must install in audio_only mode on a fixed core"
    assert pl2.is_applied() is True and pl2._mode == "audio_only"
    print("2. new core: audio_only mode - core keeps video, we keep audio")

    # video: a marked pinned run must land exactly where CORE puts it,
    # untouched by our fixup
    mmnew = sys.modules["comfy.ldm.minimax.model"]
    import torch
    text_len, latent_t, lh, lw, audio_t = 7, 7, 22, 38, 16
    fc = sum(mmnew.FRAME_PER_TOKEN[k % 5] for k in range(latent_t))
    def kf(i, marked):
        # marked = the pack's own convention: index smuggled under MC_KEY
        # with resolved_frame_index left at 0, exactly as nodes.py emits
        if marked:
            return {"resolved_frame_index": 0, pl2.MC_KEY: i,
                    "latent": torch.zeros(1, 16, 1, lh, lw)}
        return {"resolved_frame_index": i,
                "latent": torch.zeros(1, 16, 1, lh, lw)}
    plain = mmnew.PackedLayout.__new__(mmnew.PackedLayout)
    # core's own placement, no markers -> wrapper passes through untouched
    mmnew.PackedLayout.__init__(plain, text_len, latent_t, lh, lw, audio_t,
                                keyframes=[kf(0, False), kf(fc // 2, False),
                                           kf(fc - 1, False)])
    ours = mmnew.PackedLayout.__new__(mmnew.PackedLayout)
    mmnew.PackedLayout.__init__(ours, text_len, latent_t, lh, lw, audio_t,
                                keyframes=[kf(0, True), kf(fc // 2, True),
                                           kf(fc - 1, True)])
    tp = [float(plain.position_ids[a, 0])
          for a, _b, k in plain.segments if k == "cond"]
    to = [float(ours.position_ids[a, 0])
          for a, _b, k in ours.segments if k == "cond"]
    assert tp == to, ("smuggled indices must be un-smuggled to core's own "
                      "placement: %s vs %s" % (tp, to))
    assert len(set(to)) == 3, "pinned blocks stacked on one frame: %s" % to
    print("3. new core: smuggled anchors un-smuggled to core placement %s"
          % to)

    # audio: a marked audio ref must still be translated onto the timeline
    end_frame, rt = 4, 8
    ref_mc = [{"kind": "audio", "ref_audio_t": rt,
               pl2.MC_AUDIO_KEY: end_frame}]
    base = mmnew.PackedLayout.__new__(mmnew.PackedLayout)
    pl2._orig_init(base, text_len, latent_t, lh, lw, audio_t,
                   keyframes=[kf(0, False)], refs=ref_mc)
    moved_l = mmnew.PackedLayout.__new__(mmnew.PackedLayout)
    mmnew.PackedLayout.__init__(moved_l, text_len, latent_t, lh, lw,
                                audio_t, keyframes=[kf(0, False)],
                                refs=ref_mc)
    tb = base.position_ids[:, 0]
    tm = moved_l.position_ids[:, 0]
    moved_rows = [i for i in range(len(tb))
                  if float(tb[i]) != float(tm[i])]
    assert moved_rows, "marked audio ref was not translated on new core"
    deltas = {round(float(tm[i]) - float(tb[i]), 5) for i in moved_rows}
    assert len(deltas) == 1, "non-uniform audio shift: %s" % deltas
    print("4. new core: marked audio ref translated by %.3f, %d rows"
          % (deltas.pop(), len(moved_rows)))

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
    print("5. old core: keyframes restored ahead of refs -> %s" % vals)

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
    print("6. new core: payload left exactly as core built it, no writes "
          "-> %s" % vals2)

    print("all checks passed")


if __name__ == "__main__":
    main()
