"""Level-match a clip's head to the previous clip's tail, with decay.

    python seam_level_match.py clip_A.mp4 clip_B.mp4 out_B.mp4 [--decay 36]

The problem this addresses, measured rather than assumed: while a chained
clip generates its pinned region it is held to the previous clip's
content. At the trim point that constraint ends, and the model relaxes
toward its own preferred exposure -- overshooting on release and settling
back over the next second or two. The seam therefore shows an
EXPONENTIAL SETTLE, not a step and not a one-frame flash:

    A tail: 80.2  80.6  79.9  80.2  80.1  80.3  79.7
    B head: 91.1  89.6  88.4  87.5  86.4  86.0  85.7   <- decaying

Trimming another frame just exposes 89.6. A short crossfade spans a
fraction of it. What cancels it is a correction shaped like the error:
full strength at B's first frame, decaying to nothing over `--decay`
frames.

What this does NOT try to fix: each clip also wanders internally (A drifts
-4.1 over its own length in the example above, B +7.7). That is the
model's own exposure wander and belongs to a grade, not a seam fix. This
only removes the discontinuity AT the join.

The correction is a gain, not an offset, so black stays black and the
picture does not wash out. Chroma is corrected separately and more
gently, since colour shifts at a join are usually milder than luma.

Writes a new file; the input is never modified. Needs PyAV and numpy.
"""

import argparse
import sys

try:
    import av
    import numpy as np
except ImportError:
    print("needs PyAV and numpy: pip install av numpy")
    sys.exit(1)


def tail_stats(path, n):
    """Mean luma and per-channel means over a clip's last n frames."""
    frames = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            frames.append(f.to_ndarray(format="rgb24").astype(np.float32))
            if len(frames) > n:
                frames.pop(0)
    if not frames:
        raise SystemExit("no frames in %s" % path)
    stack = np.stack(frames)
    return float(np.mean(stack)), stack.mean(axis=(0, 1, 2))


def head_stats(path, n):
    frames = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            frames.append(f.to_ndarray(format="rgb24").astype(np.float32))
            if len(frames) >= n:
                break
    if not frames:
        raise SystemExit("no frames in %s" % path)
    stack = np.stack(frames)
    return float(np.mean(stack)), stack.mean(axis=(0, 1, 2))


def measure_decay(path, baseline, window, fps_hint=24):
    """Fit the head's excess luma to baseline + A*exp(-i/tau).

    Correcting an exponential error with a linear ramp leaves a visible
    undershoot in the middle, so the shape is measured rather than
    assumed. Falls back to a linear ramp when the head does not actually
    look like a decay (no excess, or it grows).
    """
    vals = []
    with av.open(path) as c:
        for i, f in enumerate(c.decode(video=0)):
            vals.append(float(np.mean(
                f.to_ndarray(format="rgb24").astype(np.float32))))
            if len(vals) >= window:
                break
    vals = np.array(vals, dtype=np.float64)
    excess = vals - baseline
    if len(excess) < 8 or excess[0] <= 0.5:
        return None, vals
    # only the positive, monotone-ish early part carries the transient
    usable = []
    for i, e in enumerate(excess):
        if e <= max(0.3, excess[0] * 0.05):
            break
        usable.append((i, e))
    if len(usable) < 5:
        return None, vals
    idx = np.array([u[0] for u in usable], dtype=np.float64)
    val = np.log(np.array([u[1] for u in usable], dtype=np.float64))
    # least squares on log space: log(e) = log(A) - i/tau
    slope, intercept = np.polyfit(idx, val, 1)
    if slope >= -1e-6:
        return None, vals
    tau = -1.0 / slope
    amp = float(np.exp(intercept))
    if not (1.0 < tau < window * 4):
        return None, vals
    return (amp, tau), vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_a", help="the clip BEFORE the join")
    ap.add_argument("clip_b", help="the clip after it, which gets corrected")
    ap.add_argument("out", help="JOINED output: clip A followed by the "
                                "corrected clip B, as one file")
    ap.add_argument("--also-plain", metavar="PATH",
                    help="write the same join WITHOUT the correction, so "
                         "you have something to compare against")
    ap.add_argument("--decay", type=int, default=36,
                    help="frames over which the correction falls to zero "
                         "(default 36 = 1.5s at 24fps)")
    ap.add_argument("--measure", type=int, default=3,
                    help="frames each side used to measure the step")
    ap.add_argument("--chroma", type=float, default=0.5,
                    help="how much of the per-channel correction to apply, "
                         "0 = luma only (default 0.5)")
    ap.add_argument("--crf", type=int, default=17)
    args = ap.parse_args()

    a_luma, a_rgb = tail_stats(args.clip_a, args.measure)
    b_luma, b_rgb = head_stats(args.clip_b, args.measure)
    if b_luma <= 0.01:
        raise SystemExit("clip B's head is black; nothing to match")

    # gain, not offset: preserves black and avoids washing the image out
    gain = a_luma / b_luma
    rgb_gain = np.where(b_rgb > 0.01, a_rgb / np.maximum(b_rgb, 1e-6), 1.0)
    # blend the per-channel correction toward the flat luma one
    rgb_gain = 1.0 + (rgb_gain - 1.0) * args.chroma \
        + (gain - 1.0) * (1.0 - args.chroma)

    print("A tail luma %.2f   B head luma %.2f   step %+.2f"
          % (a_luma, b_luma, b_luma - a_luma))

    fit, head_vals = measure_decay(args.clip_b, a_luma,
                                   max(args.decay * 2, 72))
    if fit is not None:
        amp, tau = fit
        half = tau * 0.693
        print("measured decay: excess %.2f at frame 0, tau %.1f frames "
              "(half-life %.1f frames, %.2fs at 24fps)"
              % (amp, tau, half, half / 24.0))
        span = int(min(len(head_vals), tau * 4))
        print("correcting over %d frames, exponential" % span)
    else:
        span = args.decay
        print("no clean decay found in B's head; falling back to a linear "
              "ramp over %d frames" % span)
    print("gain at frame 0: %.4f  (per channel %s)"
          % (gain, np.array2string(rgb_gain, precision=4)))

    def open_out(path, template_v, template_a):
        out = av.open(path, mode="w",
                      options={"movflags": "+faststart+use_metadata_tags"})
        vs = out.add_stream("libx264", rate=template_v.average_rate)
        vs.width, vs.height = template_v.width, template_v.height
        vs.pix_fmt = "yuv420p"
        vs.options = {"crf": str(args.crf)}
        aso = None
        if template_a is not None:
            aso = out.add_stream("aac", rate=template_a.rate)
            aso.layout = "stereo"
        return out, vs, aso

    with av.open(args.clip_a) as pa_:
        v_tmpl = pa_.streams.video[0]
        a_tmpl = pa_.streams.audio[0] if pa_.streams.audio else None
        rate = a_tmpl.rate if a_tmpl is not None else None

    targets = [(args.out, True)]
    if args.also_plain:
        targets.append((args.also_plain, False))

    corrected = []
    for path, do_fix in targets:
        out, vs, aso = open_out(path, v_tmpl, a_tmpl)
        resampler = None
        if aso is not None:
            resampler = av.AudioResampler(format="fltp", layout="stereo",
                                          rate=rate)

        def write_clip(src_path, fix):
            """Mux one clip's video (optionally corrected) then its audio."""
            n = 0
            with av.open(src_path) as src:
                for frame in src.decode(video=0):
                    arr = frame.to_ndarray(format="rgb24").astype(np.float32)
                    if fix and n < span:
                        if fit is not None:
                            w = float(np.exp(-n / fit[1]))
                        else:
                            w = 1.0 - (n / float(span))
                        g = 1.0 + (rgb_gain - 1.0) * w
                        arr = np.clip(arr * g, 0, 255)
                        if n < 3 and not corrected:
                            pass
                        if n < 3:
                            corrected.append(float(np.mean(arr)))
                    for p_ in vs.encode(av.VideoFrame.from_ndarray(
                            arr.astype(np.uint8), format="rgb24")):
                        out.mux(p_)
                    n += 1
            return n

        write_clip(args.clip_a, False)
        write_clip(args.clip_b, do_fix)
        for p_ in vs.encode():
            out.mux(p_)

        if aso is not None:
            for src_path in (args.clip_a, args.clip_b):
                with av.open(src_path) as src:
                    if not src.streams.audio:
                        continue
                    for aframe in src.decode(audio=0):
                        for r in (resampler.resample(aframe) or []):
                            r.pts = None
                            for p_ in aso.encode(r):
                                out.mux(p_)
            for p_ in aso.encode():
                out.mux(p_)
        out.close()
        print("wrote %s  (%s)"
              % (path, "level-matched at the join" if do_fix
                 else "untouched, for comparison"))

    if corrected:
        print("corrected B[0:3] luma: %s  (A tail was %.2f)"
              % (", ".join("%.2f" % x for x in corrected[:3]), a_luma))
    with av.open(args.clip_a) as c:
        a_dur = float(c.duration) / av.time_base if c.duration else 0.0
    print("\nTHE JOIN IS AT %.2fs. Scrub to a couple of seconds before "
          "that." % a_dur)
    if args.also_plain:
        print("Same timestamp in both files, so you can flip between them.")


if __name__ == "__main__":
    main()
