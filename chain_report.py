"""Measure how a chain's picture changes from clip to clip.

Each clip in a chain is conditioned on the previous clip's output, which
was itself conditioned on the one before. The model was trained to
continue real footage, so continuing its own approximation compounds a
small drift with every link - exposure wanders, texture softens or
crunches. This module turns "it seems to degrade" into numbers.

Four picture measures per clip, all on sampled frames:

  luma         mean brightness, 0-255
  contrast     standard deviation of luma - how much range is left
  sharpness    mean gradient energy - detail and micro-texture
  colour       mean saturation - how far from grey

and four sound measures, on the whole track in short windows:

  loudness     RMS level in dBFS - does the chain get quieter or louder
  brightness   spectral centroid in Hz - dull or harsh
  high_band    share of energy above 4 kHz, in % - hiss build-up or
               high-end loss
  noise_floor  level of the quietest windows in dBFS - the bed under
               the speech, which is what creeps up as a voice degrades

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
MEASURES = ("luma", "contrast", "sharpness", "colour",
            "loudness", "brightness", "high_band", "noise_floor")
DB_MEASURES = ("loudness", "noise_floor")
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


AUDIO_WINDOW_S = 0.046     # ~2048 samples at 44.1k; a phoneme, not a word
AUDIO_HIGH_HZ = 4000.0
AUDIO_FLOOR_PCT = 10       # the quietest tenth of windows is the bed


def measure_audio(path):
    """Sound statistics for one clip, or None when it has no track."""
    av, np = _require()
    chunks, rate = [], 0
    try:
        with av.open(path) as c:
            if not c.streams.audio:
                return None
            for frame in c.decode(audio=0):
                rate = rate or int(frame.sample_rate)
                arr = frame.to_ndarray()
                if arr.ndim == 2:
                    arr = arr.mean(axis=0)      # mono for measurement
                chunks.append(arr.astype(np.float32))
    except Exception:
        return None
    if not chunks or not rate:
        return None
    x = np.concatenate(chunks)
    if x.size < rate // 10:
        return None
    win = max(256, int(rate * AUDIO_WINDOW_S))
    n = x.size // win
    if n < 4:
        return None
    frames = x[:n * win].reshape(n, win)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    db = 20.0 * np.log10(rms)
    # spectral shape from the same windows, Hann-weighted
    spec = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1)) ** 2
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    total = spec.sum(axis=1) + 1e-12
    centroid = (spec * freqs).sum(axis=1) / total
    high = spec[:, freqs >= AUDIO_HIGH_HZ].sum(axis=1) / total
    # weight the spectral measures by energy, so silence does not vote
    w = rms / rms.sum()
    floor_n = max(1, n * AUDIO_FLOOR_PCT // 100)
    return {"loudness": float(20.0 * np.log10(rms.mean())),
            "brightness": float((centroid * w).sum()),
            "high_band": float((high * w).sum() * 100.0),
            "noise_floor": float(np.sort(db)[:floor_n].mean()),
            "audio_seconds": float(x.size / rate)}


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
    row = {"luma": float(np.mean(luma)),
           "contrast": float(np.mean(contrast)),
           "sharpness": float(np.mean(sharp)),
           "colour": float(np.mean(colour)),
           "frames_sampled": len(luma)}
    audio = measure_audio(path)
    if audio:
        row.update(audio)
    return row


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
    for key in MEASURES:
        series = [r[key] for r in rows if key in r]
        if len(series) != len(rows):
            continue                  # a clip without a track: no trend
        m = _slope(series)
        entry = {
            "first": series[0], "last": series[-1],
            "total": series[-1] - series[0],
            "per_clip": m,
        }
        if key in DB_MEASURES:
            # dBFS is already logarithmic: the change IS the honest
            # figure, and a ratio against a negative number means nothing
            entry["pct_total"] = None
        else:
            # as a percentage of where it started, which is the honest
            # way to compare a sharpness change against a luma change
            entry["pct_total"] = (100.0 * (series[-1] - series[0])
                                  / series[0] if series[0] else None)
        trend[key] = entry
    return {"clips": rows, "trend": trend,
            "sampled_every": SAMPLE_EVERY}
