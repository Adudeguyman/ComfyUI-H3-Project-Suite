"""Pinned audio must coexist with an upstream reference set.

A Ref2VA graph reaches the Motion Context node with `minimax_refs` already
populated by MiniMaxH3ReferenceToVideo -- voice, character images. The node
used to overwrite that key, so every extension clip silently lost the whole
reference set. Appending is only half the fix: the pinned block then sits at
the END of the ref cursor, not at text_len, so the layout patch has to find
and move its rows by its own slot.

Checks, against the mock layout:
  1. a marked audio ref appended after other refs no longer raises
  2. exactly the pinned block's rows move, uniformly
  3. the moved window still ENDS at the target coordinate the pinning asks
     for -- the property that makes the seam phase-locked
  4. upstream ref rows and the keyframe anchors are untouched
  5. an unrecognised ref kind fails loudly instead of sliding coordinates
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _mock_harness import make_mm, load_patch

TEXT_LEN, LATENT_T, LH, LW, AUDIO_T = 7, 7, 22, 38, 16


def _build(mm, pl, refs, keyframes):
    frame_count = sum(mm.FRAME_PER_TOKEN[k % 5] for k in range(LATENT_T))
    lay = mm.PackedLayout.__new__(mm.PackedLayout)
    pl._orig_init(lay, TEXT_LEN, LATENT_T, LH, LW, AUDIO_T,
                  keyframes=keyframes, refs=refs, frame_count=frame_count)
    return lay, frame_count


def main():
    mm = make_mm()
    pl = load_patch(mm)
    assert pl.apply_patch(), "self-test must still pass"

    rt, end_frame = 8, 4
    run = [{"resolved_frame_index": 0, pl.MC_KEY: i} for i in range(4)]

    pinned = {"kind": "audio", "ref_audio_t": rt, pl.MC_AUDIO_KEY: end_frame}
    refs = [{"kind": "image"},                       # character still
            {"kind": "audio", "ref_audio_t": 6},     # voice reference
            pinned]                                  # ours, appended last

    frame_count = sum(mm.FRAME_PER_TOKEN[k % 5] for k in range(LATENT_T))
    # both sides get _fixup; only _fixup_audio distinguishes them, so the
    # diff isolates the audio move rather than the anchor rewrite
    before, _ = _build(mm, pl, refs, run)
    pl._fixup(before, TEXT_LEN, LATENT_T, frame_count, run, refs=refs)
    after, _ = _build(mm, pl, refs, run)
    pl._fixup(after, TEXT_LEN, LATENT_T, frame_count, run, refs=refs)
    pl._fixup_audio(after, TEXT_LEN, refs)
    print("1. marked audio ref among %d refs placed without error" % len(refs))

    t0 = before.position_ids[:, 0]
    t1 = after.position_ids[:, 0]

    cond_rows = set()
    for a, b, kind in before.segments:
        if kind == "cond":
            cond_rows.update(range(a, b))

    slot_start = float(TEXT_LEN) + pl._ref_cursor_advance(refs[:2])
    expect_moved = set(
        i for i in range(len(t0))
        if slot_start - 1e-4 <= float(t0[i]) < slot_start + rt - 1e-4
        and i not in cond_rows)
    moved = set(i for i in range(len(t0)) if float(t0[i]) != float(t1[i]))
    assert moved, "no rows moved"
    assert moved == expect_moved, (
        "wrong rows moved: %s" % sorted(moved ^ expect_moved)[:8])
    deltas = {round(float(t1[i]) - float(t0[i]), 9) for i in moved}
    assert len(deltas) == 1, "non-uniform shift: %s" % sorted(deltas)[:6]
    print("2. %d rows moved by one uniform shift of %.4f"
          % (len(moved), deltas.pop()))

    # the semantic property, computed independently of the patch's formula:
    # the window's END must land at the target instant the pinning asks for.
    target_origin = float(TEXT_LEN) + pl._ref_cursor_advance(refs)
    want_end = target_origin + mm.FRAME_RESCALE * end_frame
    # mock emits audio ref rows at 0.5 spacing across rt units of coordinate
    got_end = max(float(t1[i]) for i in moved) + 0.5
    assert abs(got_end - want_end) < 1e-6, (
        "window ends at %.6f, target instant is %.6f" % (got_end, want_end))
    print("3. window ends at %.4f == target frame %d" % (got_end, end_frame))

    ref_rows = set()
    for a, b, kind in before.segments:
        if kind == "ref":
            ref_rows.update(range(a, b))
    untouched_refs = ref_rows - moved
    assert untouched_refs, "no upstream ref rows in the layout"
    assert all(float(t0[i]) == float(t1[i]) for i in untouched_refs)
    # keyframe anchors: _fixup moves them, but by the same amount with and
    # without our block present, so compare against a run with no pinned ref
    plain_refs = refs[:2]
    plain, _ = _build(mm, pl, plain_refs, run)
    pl._fixup(plain, TEXT_LEN, LATENT_T, frame_count, run, refs=plain_refs)
    anchors_plain = [float(plain.position_ids[a, 0])
                     for a, _, k in plain.segments if k == "cond"]
    anchors_ours = [float(t1[a]) for a, _, k in after.segments if k == "cond"]
    gaps_plain = [x - anchors_plain[0] for x in anchors_plain]
    gaps_ours = [x - anchors_ours[0] for x in anchors_ours]
    assert all(abs(p - o) < 1e-6 for p, o in zip(gaps_plain, gaps_ours)), (
        "pinned block distorted the anchor spacing: %s vs %s"
        % (gaps_plain, gaps_ours))
    print("4. %d upstream ref rows untouched, anchor spacing unchanged"
          % len(untouched_refs))

    try:
        pl._ref_cursor_advance([{"kind": "sprocket"}])
    except RuntimeError as exc:
        assert "unrecognised ref kind" in str(exc)
        print("5. unknown ref kind rejected: %s" % str(exc).split(": ", 1)[1])
    else:
        raise AssertionError("unknown ref kind was silently accepted")

    print("all checks passed")


if __name__ == "__main__":
    main()
