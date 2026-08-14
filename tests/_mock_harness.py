"""Standalone harness for patch_layout's self-test.

Fakes comfy.ldm.minimax.model with a PackedLayout that reproduces the layout
semantics the handoff documents:

  - text rows at coordinates 0..text_len-1
  - ref blocks laid out from a cursor starting at text_len; audio refs
    advance it by ref_audio_t and emit 2*ref_audio_t rows
  - cond segments per keyframe at STOCK coordinates (computed from text_len,
    NOT the cursor -- reproducing the uncompensated-anchor behaviour the
    patch exists to fix), rejecting interior anchors
  - target video rows from the cursor via _video_t_spans, target audio rows
    from the cursor at 1.0 spacing

Then imports patch_layout against the fakes and checks:
  1. apply_patch succeeds (self-test passes against faithful stock)
  2. interior anchors work after patching, stock coords for endpoints
  3. a deliberately broken ref cursor (advance = 2*ref_audio_t) makes the
     self-test FAIL, i.e. the new ref case actually detects upstream drift
  4. the mixed stock/MC + ref guard trips
  5. single-ref timeline audio still lands at the requested frame
  6. Ref2VA image refs remain in place ahead of timeline audio
"""

import importlib
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FRAME_RESCALE = 5.0 / 3.0
FRAME_PER_TOKEN = (1, 4, 4, 4, 4)


def _video_t_spans(latent_t):
    return [FRAME_RESCALE * FRAME_PER_TOKEN[k % 5] for k in range(latent_t)]


def make_mm(ref_advance_factor=1.0):
    """Build a fake comfy.ldm.minimax.model. ref_advance_factor != 1
    simulates an upstream change to how refs advance the cursor."""
    mm = types.ModuleType("comfy.ldm.minimax.model")
    mm.FRAME_RESCALE = FRAME_RESCALE
    mm.FRAME_PER_TOKEN = FRAME_PER_TOKEN
    mm._video_t_spans = _video_t_spans

    class PackedLayout:
        def __init__(self, text_len, latent_t, latent_h, latent_w, audio_t,
                     keyframes=None, refs=None, frame_count=None):
            rows_per_cond = 4  # stand-in for latent_h * latent_w
            segs, coords = [], []

            def emit(kind, ts):
                a = len(coords)
                coords.extend(ts)
                segs.append((a, len(coords), kind))

            emit("text", [float(i) for i in range(text_len)])

            cursor = float(text_len)
            for blk in (refs or []):
                kind = blk.get("kind")
                if kind == "audio":
                    rt = float(blk["ref_audio_t"]) * ref_advance_factor
                    emit("ref", [cursor + i * 0.5
                                 for i in range(2 * int(rt))] or [cursor])
                    cursor += rt
                elif kind == "image":
                    emit("ref", [cursor])
                    cursor += 1.0
                else:
                    raise ValueError("mock: unsupported ref kind %r" % kind)

            spans = _video_t_spans(latent_t)
            for kf in (keyframes or []):
                p = kf["resolved_frame_index"]
                # faithful stock: computed from text_len, NOT cursor
                if p == 0:
                    t = float(text_len)
                elif frame_count is not None and p == frame_count - 1:
                    t = float(text_len) + sum(spans) - FRAME_RESCALE
                else:
                    raise ValueError(
                        "only first/last keyframe anchors are supported")
                emit("cond", [t] * rows_per_cond)

            acc, vts = cursor, []
            for s in spans:
                vts.append(acc)
                acc += s
            emit("video", vts)
            emit("audio", [cursor + float(i) for i in range(audio_t)])

            self.segments = segs
            self.position_ids = np.zeros((len(coords), 4), dtype=np.float64)
            self.position_ids[:, 0] = coords

    mm.PackedLayout = PackedLayout
    return mm


def make_torch():
    t = types.ModuleType("torch")
    t.equal = lambda a, b: a.shape == b.shape and bool(np.array_equal(a, b))
    t.float32 = np.float32

    def _ones(shape, dtype=np.float32):
        from _node_smoke_test import T as _T  # the shared tensor fake
        return _T(np.ones(shape, dtype=np.float32))
    t.ones = _ones

    def _zeros(*shape, **kw):
        from _node_smoke_test import T as _T
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
            shape = tuple(shape[0])
        return _T(np.zeros(shape, dtype=np.float32))
    t.zeros = _zeros
    return t


def load_patch(mm):
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["torch"] = make_torch()
    sys.modules.pop("patch_layout", None)
    return importlib.import_module("patch_layout")


def main():
    # 1. faithful stock: patch must apply
    mm = make_mm()
    pl = load_patch(mm)
    assert pl.apply_patch(), "self-test failed against faithful stock"
    assert pl.is_applied()
    print("1. self-test passes against faithful stock layout")

    # 2. interior anchors now build, ref compensation lands
    text_len, latent_t, lh, lw, audio_t = 7, 7, 22, 38, 16
    fc = sum(FRAME_PER_TOKEN[k % 5] for k in range(latent_t))
    run = [{"resolved_frame_index": 0, pl.MC_KEY: i} for i in range(4)]
    lay = mm.PackedLayout(text_len, latent_t, lh, lw, audio_t,
                          keyframes=run, frame_count=fc)
    ts = [float(lay.position_ids[a, 0])
          for a, _, k in lay.segments if k == "cond"]
    exp = [text_len + FRAME_RESCALE * i for i in range(4)]
    assert np.allclose(ts, exp), (ts, exp)
    ref = [{"kind": "audio", "ref_audio_t": 8}]
    lay2 = mm.PackedLayout(text_len, latent_t, lh, lw, audio_t,
                           keyframes=run, refs=ref, frame_count=fc)
    ts2 = [float(lay2.position_ids[a, 0])
           for a, _, k in lay2.segments if k == "cond"]
    assert np.allclose(ts2, [t + 8.0 for t in ts]), (ts, ts2)
    print("2. interior run at", [round(t, 4) for t in ts],
          "-> with 8-step ref", [round(t, 4) for t in ts2])

    # 3. upstream ref-cursor change must make the self-test fail
    bad = make_mm(ref_advance_factor=2.0)
    pl_bad = load_patch(bad)
    ok = pl_bad.apply_patch()
    assert not ok, "self-test PASSED against a broken ref cursor (bad)"
    assert bad.PackedLayout.__init__.__name__ != "_patched_init"
    print("3. broken ref cursor (2x advance) is caught, patch refused")

    # 4. mixed stock/MC keyframes under a ref are rejected
    pl3 = load_patch(make_mm())
    assert pl3.apply_patch()
    mm3 = sys.modules["comfy.ldm.minimax.model"]
    mixed = [{"resolved_frame_index": 0},
             {"resolved_frame_index": 0, pl3.MC_KEY: 2}]
    try:
        mm3.PackedLayout(text_len, latent_t, lh, lw, audio_t,
                         keyframes=mixed, refs=ref, frame_count=fc)
    except RuntimeError as e:
        assert "mixed" in str(e)
        print("4. mixed stock/MC keyframes + ref rejected loudly")
    else:
        raise AssertionError("mixed keyframes under a ref were not rejected")

    # 5. audio timeline placement: ref rows must land end-aligned with the
    # given target frame, everything else untouched
    pl4 = load_patch(make_mm())
    assert pl4.apply_patch()
    mm4 = sys.modules["comfy.ldm.minimax.model"]
    run4 = [{"resolved_frame_index": 0, pl4.MC_KEY: i} for i in range(4)]
    rt, end_frame = 8, 22
    ref_mc = [{"kind": "audio", "ref_audio_t": rt,
               pl4.MC_AUDIO_KEY: end_frame}]
    lay = mm4.PackedLayout(text_len, latent_t, lh, lw, audio_t,
                           keyframes=run4, refs=ref_mc, frame_count=fc)
    ref_rows = [i for a, b, k in lay.segments if k == "ref"
                for i in range(a, b)]
    times = sorted(float(lay.position_ids[i, 0]) for i in ref_rows)
    target_origin = text_len + rt
    want_end = target_origin + FRAME_RESCALE * end_frame
    # mock lays ref rows at cursor + i*0.5 for 2*rt rows; translation
    # preserves that, so after the move: [want_end - rt, want_end - 0.5]
    assert abs(times[0] - (want_end - rt)) < 1e-6, (times[0], want_end - rt)
    assert abs(times[-1] - (want_end - 0.5)) < 1e-6, (times[-1], want_end)
    # video anchors still ref-compensated, untouched by the audio move
    ts4 = [float(lay.position_ids[a, 0])
           for a, _, k in lay.segments if k == "cond"]
    assert np.allclose(
        ts4, [target_origin + FRAME_RESCALE * i for i in range(4)]), ts4
    print("5. audio rows end-aligned at target frame %d "
          "(t %.3f..%.3f), video anchors unmoved" %
          (end_frame, times[0], times[-1]))

    # 6. existing Ref2VA refs can precede the marked MC audio block. Only
    # that final audio segment moves; the image refs retain stock positions.
    pl5 = load_patch(make_mm())
    assert pl5.apply_patch()
    mm5 = sys.modules["comfy.ldm.minimax.model"]
    run5 = [{"resolved_frame_index": 0, pl5.MC_KEY: i} for i in range(4)]
    existing_audio_t = 3
    refs5 = [
        {"kind": "image"},
        {"kind": "image"},
        {"kind": "audio", "ref_audio_t": existing_audio_t},
        {"kind": "audio", "ref_audio_t": rt,
         pl5.MC_AUDIO_KEY: end_frame},
    ]
    lay5 = mm5.PackedLayout(text_len, latent_t, lh, lw, audio_t,
                            keyframes=run5, refs=refs5, frame_count=fc)
    ref_segments = [(a, b) for a, b, k in lay5.segments if k == "ref"]
    assert len(ref_segments) == 4
    img_times = [float(lay5.position_ids[a, 0])
                 for a, _ in ref_segments[:2]]
    assert np.allclose(img_times, [text_len, text_len + 1]), img_times
    a, b = ref_segments[2]
    ordinary_audio_times = sorted(
        float(lay5.position_ids[i, 0]) for i in range(a, b))
    assert np.allclose(
        ordinary_audio_times,
        [text_len + 2 + i * 0.5 for i in range(2 * existing_audio_t)])
    a, b = ref_segments[-1]
    audio_times = sorted(float(lay5.position_ids[i, 0]) for i in range(a, b))
    target_origin = text_len + 2 + existing_audio_t + rt
    want_end = target_origin + FRAME_RESCALE * end_frame
    assert abs(audio_times[0] - (want_end - rt)) < 1e-6
    assert abs(audio_times[-1] - (want_end - 0.5)) < 1e-6
    print("6. Ref2VA image refs preserved at %s and ordinary audio at "
          "%.3f..%.3f; appended MC audio moved to %.3f..%.3f" %
          (img_times, ordinary_audio_times[0], ordinary_audio_times[-1],
           audio_times[0], audio_times[-1]))

    print("all checks passed")


if __name__ == "__main__":
    main()
