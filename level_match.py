"""Cancel the release overshoot at a chain join.

A chained clip is held to the previous clip's content while it generates
its pinned region. At the trim point that constraint ends and the model
relaxes toward its own preferred exposure, overshooting on release and
settling back over the next second or two. Measured on a real join:

    A tail: 80.2  80.6  79.9  80.2  80.1  80.3  79.7
    B head: 91.1  89.6  88.4  87.5  86.4  86.0  85.7   <- decaying

The error is an exponential settle, so the correction is shaped the same
way: full strength at B's first frame, decaying with the measured time
constant. A gain is used rather than an offset so black stays black.

This module has no ComfyUI dependency and needs av + numpy. Import is
deliberately lazy at the call sites so a pack without PyAV still loads.
"""

import logging
import os

_LOG = logging.getLogger(__name__)

MEASURE_FRAMES = 3          # frames each side used to size the step
MIN_STEP = 1.0              # below this, a join is not worth touching
MAX_GAIN_DEV = 0.35         # refuse corrections beyond +/-35%


def _require():
    try:
        import av
        import numpy as np
    except ImportError:
        raise RuntimeError(
            "h3_suite: level matching needs PyAV and numpy "
            "(pip install av numpy).")
    return av, np


def _tail(path, n):
    av, np = _require()
    frames = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            frames.append(f.to_ndarray(format="rgb24").astype(np.float32))
            if len(frames) > n:
                frames.pop(0)
    if not frames:
        raise RuntimeError("no frames in %s" % path)
    st = np.stack(frames)
    return float(np.mean(st)), st.mean(axis=(0, 1, 2))


def _head(path, n):
    av, np = _require()
    frames = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            frames.append(f.to_ndarray(format="rgb24").astype(np.float32))
            if len(frames) >= n:
                break
    if not frames:
        raise RuntimeError("no frames in %s" % path)
    st = np.stack(frames)
    return float(np.mean(st)), st.mean(axis=(0, 1, 2))


def _fit_decay(path, baseline, window):
    """Fit the head's excess luma to baseline + A*exp(-i/tau)."""
    av, np = _require()
    vals = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            vals.append(float(np.mean(
                f.to_ndarray(format="rgb24").astype(np.float32))))
            if len(vals) >= window:
                break
    excess = np.array(vals, dtype=np.float64) - baseline
    if len(excess) < 8 or excess[0] <= 0.5:
        return None
    usable = []
    for i, e in enumerate(excess):
        if e <= max(0.3, excess[0] * 0.05):
            break
        usable.append((i, e))
    if len(usable) < 5:
        return None
    idx = np.array([u[0] for u in usable], dtype=np.float64)
    logv = np.log(np.array([u[1] for u in usable], dtype=np.float64))
    slope, intercept = np.polyfit(idx, logv, 1)
    if slope >= -1e-6:
        return None
    tau = -1.0 / slope
    if not (1.0 < tau < window * 4):
        return None
    return float(np.exp(intercept)), float(tau)


def measure(prev_path, next_path, window=144):
    """What the join needs, without changing anything.

    Returns a dict, or None when the step is too small to bother with.
    """
    av, np = _require()
    a_luma, a_rgb = _tail(prev_path, MEASURE_FRAMES)
    b_luma, b_rgb = _head(next_path, MEASURE_FRAMES)
    step = b_luma - a_luma
    if abs(step) < MIN_STEP or b_luma <= 0.01:
        return None
    gain = a_luma / b_luma
    if abs(gain - 1.0) > MAX_GAIN_DEV:
        # a step this large is a different problem (a cut, a lighting
        # change the prompt asked for); silently "fixing" it would be
        # worse than leaving it alone
        _LOG.warning("h3_suite: join step %+.1f is too large to level "
                     "match; leaving it alone", step)
        return None
    rgb_gain = np.where(b_rgb > 0.01, a_rgb / np.maximum(b_rgb, 1e-6), 1.0)
    rgb_gain = 1.0 + (rgb_gain - 1.0) * 0.5 + (gain - 1.0) * 0.5
    fit = _fit_decay(next_path, a_luma, window)
    span = int(min(window, fit[1] * 4)) if fit else 36
    return {"a_luma": a_luma, "b_luma": b_luma, "step": step,
            "gain": float(gain), "rgb_gain": rgb_gain,
            "tau": (fit[1] if fit else None), "span": span}


def correct(prev_path, next_path, out_path, crf=17, plan=None):
    """Write a level-matched copy of next_path. Returns the plan used."""
    av, np = _require()
    plan = plan or measure(prev_path, next_path)
    if plan is None:
        return None
    rgb_gain, span, tau = plan["rgb_gain"], plan["span"], plan["tau"]

    with av.open(next_path) as src:
        vs_in = src.streams.video[0]
        as_in = src.streams.audio[0] if src.streams.audio else None
        out = av.open(out_path, mode="w",
                      options={"movflags": "+faststart+use_metadata_tags"})
        for k, v in dict(src.metadata).items():
            try:
                out.metadata[k] = v
            except Exception:
                pass
        vs = out.add_stream("libx264", rate=vs_in.average_rate)
        vs.width, vs.height, vs.pix_fmt = vs_in.width, vs_in.height, "yuv420p"
        vs.options = {"crf": str(crf)}
        aso = None
        if as_in is not None:
            aso = out.add_stream("aac", rate=as_in.rate)
            aso.layout = "stereo"
        i = 0
        for frame in src.decode(video=0):
            arr = frame.to_ndarray(format="rgb24").astype(np.float32)
            if i < span:
                w = float(np.exp(-i / tau)) if tau else \
                    1.0 - (i / float(span))
                arr = np.clip(arr * (1.0 + (rgb_gain - 1.0) * w), 0, 255)
            for p in vs.encode(av.VideoFrame.from_ndarray(
                    arr.astype(np.uint8), format="rgb24")):
                out.mux(p)
            i += 1
        for p in vs.encode():
            out.mux(p)
        if aso is not None:
            with av.open(next_path) as src2:
                for af in src2.decode(audio=0):
                    af.pts = None
                    for p in aso.encode(af):
                        out.mux(p)
                for p in aso.encode():
                    out.mux(p)
        out.close()
    _LOG.info("h3_suite: level matched %s at its head (step %+.1f, "
              "tau %s)", os.path.basename(next_path), plan["step"],
              ("%.1f" % tau) if tau else "linear")
    return plan
