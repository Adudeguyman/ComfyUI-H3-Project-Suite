"""Measure how a chain's picture changes from clip to clip.

Each clip in a chain is conditioned on the previous clip's output, which
was itself conditioned on the one before. The model was trained to
continue real footage, so continuing its own approximation compounds a
small drift with every link - exposure wanders, texture softens or
crunches. This module turns "it seems to degrade" into numbers.

Four measures per clip, all on sampled frames:

  luma         mean brightness, 0-255
  contrast     standard deviation of luma - how much range is left
  sharpness    mean gradient energy - detail and micro-texture
  colour       mean saturation - how far from grey

None of these is a quality score. They are descriptive statistics that
also move when the CONTENT changes: a clip that cuts to a dark interior
drops luma honestly, and a clip with a busy background is sharper by
this measure without being better. What matters is the trend across
many clips of one continuous scene, not any single number.

The per-clip slope is fitted by least squares and reported as change
per clip, with the total across the chain.

Needs av + numpy; imports lazily so the pack loads without them.
"""

import logging

_LOG = logging.getLogger(__name__)

SAMPLE_EVERY = 8          # frames; ~3 per second at 24fps
MAX_SAMPLES = 40          # per clip, enough for a stable mean


def _require():
    try:
        import av
        import numpy as np
    except ImportError:
        raise RuntimeError(
            "h3_suite: drift measurement needs PyAV and numpy "
            "(pip install av numpy).")
    return av, np


def measure_clip(path):
    """Sampled statistics for one clip."""
    av, np = _require()
    luma, contrast, sharp, colour = [], [], [], []
    with av.open(path) as c:
        for i, frame in enumerate(c.decode(video=0)):
            if i % SAMPLE_EVERY:
                continue
            if len(luma) >= MAX_SAMPLES:
                break
            rgb = frame.to_ndarray(format="rgb24").astype(np.float32)
            g = rgb.mean(axis=2)
            luma.append(float(g.mean()))
            contrast.append(float(g.std()))
            # gradient energy: detail and micro-texture. Absolute
            # differences rather than a Laplacian, which is noisier on
            # compressed footage.
            dx = np.abs(np.diff(g, axis=1)).mean()
            dy = np.abs(np.diff(g, axis=0)).mean()
            sharp.append(float((dx + dy) / 2.0))
            mx = rgb.max(axis=2)
            mn = rgb.min(axis=2)
            colour.append(float(np.mean(
                np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0))
                * 100.0))
    if not luma:
        return None
    return {"luma": float(np.mean(luma)),
            "contrast": float(np.mean(contrast)),
            "sharpness": float(np.mean(sharp)),
            "colour": float(np.mean(colour)),
            "frames_sampled": len(luma)}


def _slope(values):
    """Least-squares change per step; None when there is too little."""
    av, np = _require()
    if len(values) < 3:
        return None
    x = np.arange(len(values), dtype=np.float64)
    y = np.array(values, dtype=np.float64)
    m, _b = np.polyfit(x, y, 1)
    return float(m)


def measure_chain(paths, labels=None):
    """Statistics for every clip, plus the fitted trend across them."""
    av, np = _require()
    rows = []
    for i, p in enumerate(paths):
        st = measure_clip(p)
        if st is None:
            continue
        st["index"] = i + 1
        st["label"] = (labels[i] if labels and i < len(labels)
                       else "clip %d" % (i + 1))
        rows.append(st)
    if not rows:
        return {"clips": [], "trend": {}, "note": "nothing to measure"}
    trend = {}
    for key in ("luma", "contrast", "sharpness", "colour"):
        series = [r[key] for r in rows]
        m = _slope(series)
        trend[key] = {
            "first": series[0], "last": series[-1],
            "total": series[-1] - series[0],
            "per_clip": m,
            # as a percentage of where it started, which is the honest
            # way to compare a sharpness change against a luma change
            "pct_total": (100.0 * (series[-1] - series[0]) / series[0]
                          if series[0] else None),
        }
    return {"clips": rows, "trend": trend,
            "sampled_every": SAMPLE_EVERY}
