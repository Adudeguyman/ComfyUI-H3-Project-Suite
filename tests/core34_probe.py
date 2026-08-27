"""The pack must run correctly on all three layout shapes.

0.31-0.33.0: first/last restriction, frame_count present -> full patch.
0.33.x master (#15439): interior anchors native -> audio wrapper only.
0.34.0: anchors AND keyframe audio native, no frame_count -> NOTHING
installed; the nodes emit plain keyframes with a fractional anchor.

Detection is behavioural throughout, so this drives the real
apply_patch() against faithful mocks of each shape and checks what got
installed, what the node emits, and where the audio actually lands.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _mock_harness import make_mm, make_mm_034, make_torch  # noqa: E402

FR = make_mm().FRAME_RESCALE


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
    # --- 0.34: nothing installed, everything covered ---
    mm = make_mm_034()
    install(mm)
    pl = fresh()
    stock = mm.PackedLayout.__init__
    assert pl.apply_patch() is False
    assert mm.PackedLayout.__init__ is stock, "0.34 core was patched"
    assert pl.is_applied() is False
    assert pl.is_covered() is True, "native coverage must count as covered"
    assert pl.audio_keyframes_native() is True
    print("1. 0.34 core: nothing installed, covered, audio native")

    # --- and the audio keyframe layout does what the node relies on:
    # an end-aligned window built from a fractional (negative) anchor ---
    torch = sys.modules["torch"]
    rt = 6
    end_frame = 2.5
    start = end_frame - rt * (24.0 / 40.0)      # -1.1: negative on purpose
    kf = [{"resolved_frame_index": start,
           "audio_latent": torch.zeros(1, 32, 2, rt)}]
    lay = mm.PackedLayout(7, 7, 2, 2, 16, keyframes=kf)
    rows = [(a, b) for a, b, kind in lay.segments if kind == "cond_audio"]
    assert rows, "no cond_audio rows from an audio keyframe"
    first = float(lay.position_ids[rows[0][0], 0])
    want_first = 7 + FR * start
    assert abs(first - want_first) < 1e-6, (first, want_first)
    print("2. fractional negative anchor placed literally "
          "(start %.2f -> position %.3f)" % (start, first))

    # --- 0.33-with-#15439 (interior native, frame_count present):
    # the audio wrapper installs, exactly as before ---
    mm2 = make_mm()
    # lift the restriction the way #15439 did, keeping frame_count
    base = mm2.PackedLayout.__init__

    def lifted(self, text_len, latent_t, lh, lw, audio_t, keyframes=None,
               refs=None, frame_count=None):
        fixed = []
        for k in (keyframes or []):
            k = dict(k)
            fixed.append(k)
        # place interior anchors by index, first/last via the old body
        try:
            base(self, text_len, latent_t, lh, lw, audio_t,
                 keyframes=None, refs=refs, frame_count=frame_count)
        except Exception:
            raise
        segs = list(self.segments)
        coords = list(self.position_ids[:, 0])
        for k in fixed:
            p = float(k["resolved_frame_index"])
            t = float(text_len) + FR * p
            a = len(coords)
            coords.extend([t] * 4)
            segs.append((a, len(coords), "cond"))
        self.segments = segs
        import numpy as np
        self.position_ids = np.zeros((len(coords), 4), dtype=np.float64)
        self.position_ids[:, 0] = coords

    mm2.PackedLayout.__init__ = lifted
    install(mm2)
    pl2 = fresh()
    assert pl2.apply_patch() is True, "the audio wrapper should install"
    assert pl2._mode == "audio_only", pl2._mode
    assert pl2.audio_keyframes_native() is False
    print("3. #15439-era core: audio wrapper installs, as before")

    # --- plain 0.33: the full patch installs, as always ---
    mm3 = make_mm()
    install(mm3)
    pl3 = fresh()
    assert pl3.apply_patch() is True
    assert pl3._mode == "full", pl3._mode
    print("4. 0.33 core: full patch, unchanged")

    print("all checks passed")


if __name__ == "__main__":
    main()
