"""Video seam probe: measure what actually happens at a join.

    python video_seam_probe.py clip_006_take4.mp4 clip_007_take9.mp4

Give it two ADJACENT delivered clips from a project's clips/ folder, in
order. Both are post-trim, so clip B starts where clip A ends.

A join can go wrong in four distinct ways and they need different fixes,
so the probe separates them rather than reporting one "smoothness" number:

  DUPLICATE   B's first frame repeats A's last. Reads as a hitch or
              stutter. Means one frame too few was trimmed.
  SKIP        the boundary jump is far larger than the motion on either
              side. Means one frame too many was trimmed, or the clip
              genuinely cuts.
  DRIFT       the boundary is smooth in motion terms but the overall
              brightness or colour steps. The model's regeneration of the
              pinned frames wandered slightly before the handoff.
  CLEAN       boundary difference sits inside the range of normal
              frame-to-frame motion.

The baseline matters: a 40-unit jump is alarming in a locked-off shot and
unremarkable in a whip pan, so everything is reported RELATIVE to the
motion already present in the surrounding frames.

Limitation worth knowing: the measure is frame difference, so on highly
repetitive content (a spinning wheel, a looping pattern, a locked shot
with flicker) a skipped frame can land somewhere that happens to look
close, and read as CLEAN. The DUPLICATE and DRIFT verdicts do not have
that weakness. If motion is repetitive, trust your eyes over this.

Needs PyAV and numpy.
"""

import argparse
import sys

try:
    import av
    import numpy as np
except ImportError:
    print("needs PyAV and numpy: pip install av numpy")
    sys.exit(1)


def read_edge(path, head=None, tail=None):
    """Decode the first `head` or last `tail` frames as float arrays."""
    frames = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            arr = f.to_ndarray(format="rgb24").astype(np.float32)
            frames.append(arr)
            if head is not None and len(frames) >= head:
                break
    if tail is not None:
        frames = frames[-tail:]
    return frames


def mad(a, b):
    """Mean absolute difference, 0-255 scale."""
    return float(np.mean(np.abs(a - b)))


def luma(f):
    return float(np.mean(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_a")
    ap.add_argument("clip_b")
    ap.add_argument("--window", type=int, default=6,
                    help="frames either side to use as the motion baseline")
    args = ap.parse_args()

    n = max(3, args.window)
    a = read_edge(args.clip_a, tail=n + 1)
    b = read_edge(args.clip_b, head=n + 1)
    if len(a) < 3 or len(b) < 3:
        print("clips too short to measure")
        return 1
    if a[-1].shape != b[0].shape:
        print("resolution differs: %s vs %s" % (a[-1].shape, b[0].shape))
        return 1

    a_motion = [mad(a[i], a[i + 1]) for i in range(len(a) - 1)]
    b_motion = [mad(b[i], b[i + 1]) for i in range(len(b) - 1)]
    baseline = float(np.median(a_motion + b_motion))
    seam = mad(a[-1], b[0])

    print("clip A ...%s" % args.clip_a.split("/")[-1])
    print("clip B    %s\n" % args.clip_b.split("/")[-1])
    print("motion within A (last %d frames): %s"
          % (len(a_motion), ", ".join("%.1f" % x for x in a_motion)))
    print("motion within B (first %d frames): %s"
          % (len(b_motion), ", ".join("%.1f" % x for x in b_motion)))
    print("median baseline motion: %.2f" % baseline)
    print("difference ACROSS the seam: %.2f  (%.2fx baseline)\n"
          % (seam, seam / baseline if baseline else float("inf")))

    # off-by-one checks: does A's last frame match one of B's other frames
    # better than it matches B's first?
    cands = [(mad(a[-1], b[i]), i) for i in range(min(3, len(b)))]
    best = min(cands)
    print("A[-1] vs B[0]=%.2f  B[1]=%.2f  B[2]=%.2f"
          % (cands[0][0], cands[1][0], cands[2][0]))

    # brightness step, which motion alone will not reveal
    la = luma(a[-1])
    lb = luma(b[0])
    print("mean brightness: A[-1]=%.1f  B[0]=%.1f  step=%+.1f\n"
          % (la, lb, lb - la))

    verdict = []
    if seam < baseline * 0.25:
        verdict.append(
            "DUPLICATE: B's first frame is nearly identical to A's last. "
            "One frame too few was trimmed; the join will read as a hitch.")
    elif best[1] == 1 and cands[1][0] < cands[0][0] * 0.6:
        verdict.append(
            "DUPLICATE (offset): A's last frame matches B[1] better than "
            "B[0], so B[0] is a repeat. Trim one more frame.")
    elif seam > baseline * 2.5:
        verdict.append(
            "SKIP or CUT: the seam jump is %.1fx the surrounding motion. "
            "Either a frame was lost, or the model did not continue the "
            "shot." % (seam / baseline if baseline else 0))
    else:
        verdict.append(
            "CLEAN in motion terms: the seam difference sits inside the "
            "range of normal frame-to-frame movement.")

    if abs(lb - la) > 2.0:
        verdict.append(
            "DRIFT: brightness steps by %+.1f across the join. The model's "
            "regeneration of the pinned frames wandered before the handoff. "
            "A longer context length anchors it harder; a short crossfade "
            "in an editor hides what remains." % (lb - la))

    print("\n".join("-> " + v for v in verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
