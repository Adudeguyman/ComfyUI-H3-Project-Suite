"""Consecutive level-matched joins must not compound.

The worry this guards: if clip 2 is corrected toward clip 1, does the
clip 3 correction still measure against clip 2's ORIGINAL level and
therefore land wrong? Two things prevent it, and both are checked here:

  1. the correction is head-only - it decays to nothing well before the
     clip's tail, which is the level the next join measures against
  2. export feeds the corrected file forward, so even a correction that
     DID reach the tail would be measured correctly

Needs PyAV and numpy; skips cleanly without them.
"""

import os
import sys
import tempfile

try:
    import av
    import numpy as np
except ImportError:
    print("PyAV or numpy unavailable; skipping level match chain probe")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

W, H, FPS = 128, 96, 24


def mkclip(path, base, overshoot=0.0, tau=20.0, n=72, t0=0):
    c = av.open(path, mode="w")
    vs = c.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    vs.options = {"crf": "12"}
    for i in range(n):
        lvl = base + overshoot * np.exp(-i / tau)
        a = np.full((H, W, 3), lvl, np.float32)
        x = int(((t0 + i) * 3) % (W - 12))
        a[:, x:x + 12, :] = np.clip(lvl + 90, 0, 255)
        c.mux(vs.encode(av.VideoFrame.from_ndarray(
            np.clip(a, 0, 255).astype(np.uint8), format="rgb24")))
    for p in vs.encode():
        c.mux(p)
    c.close()


def lumas(path):
    v = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            v.append(float(np.mean(
                f.to_ndarray(format="rgb24").astype(np.float32))))
    return v


def main():
    import level_match as LM
    d = tempfile.mkdtemp()

    # --- regime 1: realistic clip length (correction dies out early) ---
    long_paths = []
    for i, (ov, t0) in enumerate(((0.0, 0), (12.0, 200), (12.0, 400)), 1):
        p = os.path.join(d, "L%d.mp4" % i)
        mkclip(p, 76, overshoot=ov, t0=t0, n=200)
        long_paths.append(p)
    plan = LM.measure(long_paths[0], long_paths[1])
    assert plan and not plan["reaches_tail"], plan
    print("1. 200-frame clips: correction spans %d frames, well inside the "
          "clip, so the tail is untouched" % plan["span"])

    fixed2 = os.path.join(d, "L2_fixed.mp4")
    LM.correct(long_paths[0], long_paths[1], fixed2)
    # re-encoding alone shifts luma by around a unit, so compare against a
    # control that is re-encoded WITHOUT any correction rather than
    # against the source
    ctrl = os.path.join(d, "L2_ctrl.mp4")
    flat = dict(plan); flat["rgb_gain"] = plan["rgb_gain"] * 0 + 1.0
    LM.correct(long_paths[0], long_paths[1], ctrl, plan=flat)
    t_ctrl, t_fixed = lumas(ctrl)[-1], lumas(fixed2)[-1]
    assert plan["span"] < plan["frames"], (plan["span"], plan["frames"])
    assert abs(t_fixed - t_ctrl) < 0.3, (t_ctrl, t_fixed)
    print("2. clip 2's tail is %.2f corrected vs %.2f re-encoded only - the "
          "correction ended %d frames before it, so the level clip 3 "
          "measures against did not move"
          % (t_fixed, t_ctrl, plan["frames"] - plan["span"]))

    a = LM.measure(long_paths[1], long_paths[2])
    b = LM.measure(fixed2, long_paths[2])
    assert abs(a["step"] - b["step"]) < 1.2, (a["step"], b["step"])
    print("3. join 3 measures %+.2f against the original clip 2 and %+.2f "
          "against the corrected one - same correction either way"
          % (a["step"], b["step"]))

    fixed3 = os.path.join(d, "L3_fixed.mp4")
    LM.correct(fixed2, long_paths[2], fixed3)
    for label, prev, cur in (("2", long_paths[0], fixed2),
                             ("3", fixed2, fixed3)):
        pv, cv = lumas(prev), lumas(cur)
        step = cv[0] - float(np.mean(pv[-3:]))
        assert abs(step) < 1.5, (label, step)
        print("4. join %s after correction: step %+.2f" % (label, step))
    assert abs(lumas(fixed3)[-1] - lumas(long_paths[0])[-1]) < 1.5
    print("5. clip 3 still settles where clip 1 did - no accumulation")

    # --- regime 2: short clips, where the correction DOES reach the tail ---
    short = []
    for i, (ov, t0) in enumerate(((0.0, 0), (12.0, 60), (12.0, 120)), 1):
        p = os.path.join(d, "S%d.mp4" % i)
        mkclip(p, 76, overshoot=12.0 if ov else 0.0, t0=t0, n=60)
        short.append(p)
    sp = LM.measure(short[0], short[1])
    assert sp and sp["reaches_tail"], sp
    print("6. 60-frame clips: correction spans %d of %d frames, so it IS "
          "still fading at the tail - the preview flags this"
          % (sp["span"], sp["frames"]))
    sfix2 = os.path.join(d, "S2_fixed.mp4")
    LM.correct(short[0], short[1], sfix2)
    orig = LM.measure(short[1], short[2])
    corr = LM.measure(sfix2, short[2])
    assert abs(orig["step"] - corr["step"]) > 0.4, (orig["step"], corr["step"])
    print("7. here the two disagree (%+.2f vs %+.2f), which is exactly why "
          "export measures against the CORRECTED file, not the original"
          % (orig["step"], corr["step"]))
    sfix3 = os.path.join(d, "S3_fixed.mp4")
    LM.correct(sfix2, short[2], sfix3)
    step = lumas(sfix3)[0] - float(np.mean(lumas(sfix2)[-3:]))
    assert abs(step) < 1.5, step
    print("8. chaining off the corrected file still lands join 3 at %+.2f"
          % step)

    print("all checks passed")


if __name__ == "__main__":
    main()
