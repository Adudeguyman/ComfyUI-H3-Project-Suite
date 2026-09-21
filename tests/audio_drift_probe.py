"""The drift report's sound measures must move the way a degrading voice does.

Three clips of the same tone, each a little quieter and with more hiss
than the last, the way a chained voice drifts. Loudness must fall, the
high band and noise floor must rise, and the trend must say so. A clip
with no sound at all must still measure its picture and simply carry no
sound rows, without breaking the chain's trend.
"""

import os
import sys
import tempfile

import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

import av  # noqa: E402

from chain_report import measure_audio, measure_chain  # noqa: E402

FPS, SR, W, H = 24, 32000, 64, 64


def write_clip(path, seconds, level_db, hiss_db, with_audio=True):
    out = av.open(path, mode="w")
    vs = out.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    vs.options = {"crf": "30", "preset": "ultrafast"}
    aso = None
    if with_audio:
        aso = out.add_stream("aac", rate=SR)
        aso.layout = "stereo"
    img = np.full((H, W, 3), 128, dtype=np.uint8)
    for _ in range(int(seconds * FPS)):
        for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
    for p in vs.encode():
        out.mux(p)
    if aso is not None:
        n = int(seconds * SR)
        t = np.arange(n) / SR
        rng = np.random.default_rng(7)
        tone = np.sin(2 * np.pi * 220 * t) * (10 ** (level_db / 20.0))
        # a voice-like envelope: on for 300 ms, off for 200 ms, so the
        # quiet gaps expose the bed the way pauses in speech do
        gate = ((t % 0.5) < 0.3).astype(np.float32)
        hiss = rng.standard_normal(n) * (10 ** (hiss_db / 20.0))
        wav = (tone * gate + hiss).astype(np.float32)
        wav = np.stack([wav, wav])
        pts = 0
        for s in range(0, n, 1024):
            seg = np.ascontiguousarray(wav[:, s:s + 1024])
            af = av.AudioFrame.from_ndarray(seg, format="fltp", layout="stereo")
            af.sample_rate = SR
            af.pts = pts
            pts += seg.shape[1]
            for p in aso.encode(af):
                out.mux(p)
        for p in aso.encode():
            out.mux(p)
    out.close()


def main():
    d = tempfile.mkdtemp()
    specs = [(-12.0, -60.0), (-15.0, -45.0), (-18.0, -35.0)]
    paths = []
    for i, (lvl, hiss) in enumerate(specs):
        p = os.path.join(d, "clip_%03d.mp4" % (i + 1))
        write_clip(p, 3.0, lvl, hiss)
        paths.append(p)

    rows = [measure_audio(p) for p in paths]
    assert all(rows), rows
    for r in rows:
        for k in ("loudness", "brightness", "high_band", "noise_floor"):
            assert k in r and np.isfinite(r[k]), (k, r)
    print("1. every clip measured: " + ", ".join(
        "%.1f dB / floor %.1f dB / high %.1f%%" % (r["loudness"], r["noise_floor"], r["high_band"])
        for r in rows))

    loud = [r["loudness"] for r in rows]
    floor = [r["noise_floor"] for r in rows]
    high = [r["high_band"] for r in rows]
    assert loud[0] > loud[1] > loud[2], loud
    assert floor[0] < floor[1] < floor[2], floor
    assert high[0] < high[1] < high[2], high
    print("2. quieter tone + more hiss -> loudness falls, noise floor and "
          "high band rise, in order")

    out = measure_chain(paths)
    t = out["trend"]
    for k in ("loudness", "brightness", "high_band", "noise_floor", "luma"):
        assert k in t, (k, list(t))
    assert t["loudness"]["total"] < -3.0, t["loudness"]
    assert t["noise_floor"]["total"] > 10.0, t["noise_floor"]
    assert t["loudness"]["pct_total"] is None and t["noise_floor"]["pct_total"] is None
    assert t["high_band"]["pct_total"] is not None
    print("3. chain trend carries the sound rows; dB rows report a change, "
          "not a percentage (loudness %+.1f dB, floor %+.1f dB)"
          % (t["loudness"]["total"], t["noise_floor"]["total"]))

    silent = os.path.join(d, "silent.mp4")
    write_clip(silent, 2.0, 0, 0, with_audio=False)
    assert measure_audio(silent) is None
    out2 = measure_chain(paths + [silent])
    assert len(out2["clips"]) == 4
    assert "luma" in out2["trend"] and "loudness" not in out2["trend"]
    assert "loudness" not in out2["clips"][-1]
    print("4. a clip with no track measures its picture, carries no sound "
          "rows, and drops the sound trend rather than breaking it")

    print("all checks passed")


if __name__ == "__main__":
    main()
