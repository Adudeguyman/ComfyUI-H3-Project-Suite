"""The PyAV join must be what the ffmpeg concat used to be.

Three clips of different solid colours, each with a tone, are joined
both ways. The copy join must keep every frame (the count is the sum),
put each clip's first frame exactly where the previous clip ended, keep
the sound the same length as the picture, and never move a packet
backwards. The re-encode join must produce the same frame count and the
same colour at every boundary.
"""

import os
import sys
import tempfile
from fractions import Fraction

import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

import av  # noqa: E402

from concat import concat_copy, concat_reencode  # noqa: E402

FPS = 24
SR = 48000
W = H = 64


def write_clip(path, frames, rgb, hz):
    out = av.open(path, mode="w", options={"movflags": "+faststart"})
    vs = out.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    vs.options = {"crf": "17", "preset": "fast"}
    aso = out.add_stream("aac", rate=SR)
    aso.layout = "stereo"
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[...] = rgb
    for _ in range(frames):
        for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
    for p in vs.encode():
        out.mux(p)
    n = int(round(frames / FPS * SR))
    t = np.arange(n) / SR
    tone = (0.2 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    wav = np.stack([tone, tone])
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


def frame_colours(path):
    cols = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            arr = f.to_ndarray(format="rgb24")
            cols.append(tuple(int(x) for x in arr.reshape(-1, 3).mean(0)))
    return cols


def stream_stats(path):
    with av.open(path) as c:
        v, a = c.streams.video[0], c.streams.audio[0]
        vdur = Fraction(int(v.duration)) * v.time_base
        adur = Fraction(int(a.duration)) * a.time_base
        # packets must never move backwards on either stream
        last = {}
        for pkt in c.demux():
            if pkt.dts is None:
                continue
            k = pkt.stream.index
            assert k not in last or pkt.dts > last[k], (
                "dts went backwards on stream %d: %s after %s"
                % (k, pkt.dts, last[k]))
            last[k] = pkt.dts
    n_audio = 0
    with av.open(path) as c:          # demux above ran the file out
        for af in c.decode(audio=0):
            n_audio += af.samples
    return float(vdur), float(adur), n_audio


def close(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def main():
    d = tempfile.mkdtemp()
    specs = [(30, (200, 30, 30), 440.0), (25, (30, 200, 30), 660.0),
             (37, (30, 30, 200), 880.0)]
    paths = []
    for i, (n, rgb, hz) in enumerate(specs):
        p = os.path.join(d, "clip_%03d_take1.mp4" % (i + 1))
        write_clip(p, n, rgb, hz)
        paths.append(p)
    total = sum(n for n, _r, _h in specs)

    # 1. stream copy
    out = os.path.join(d, "copy.mp4")
    concat_copy(paths, out)
    cols = frame_colours(out)
    assert len(cols) == total, "copy: %d frames, expected %d" % (len(cols),
                                                                 total)
    at = 0
    for n, rgb, _hz in specs:
        assert close(cols[at], rgb), (at, cols[at], rgb)
        assert close(cols[at + n - 1], rgb), (at + n - 1, cols[at + n - 1])
        at += n
    vdur, adur, n_audio = stream_stats(out)
    assert abs(vdur - total / FPS) < 1.0 / FPS, (vdur, total / FPS)
    assert abs(adur - vdur) < 0.05, "audio %.3fs vs video %.3fs" % (adur, vdur)
    print("1. copy join: %d frames, boundaries exact, video %.3fs audio "
          "%.3fs, %d samples, timestamps monotonic"
          % (total, vdur, adur, n_audio))

    # copied packets must be the originals: compare the first clip's
    # video packets with the join's first packets byte for byte
    with av.open(paths[0]) as a, av.open(out) as b:
        pa = [bytes(p) for p in a.demux(video=0) if p.size][:5]
        pb = [bytes(p) for p in b.demux(video=0) if p.size][:5]
    assert pa == pb, "copy join re-encoded the picture"
    print("2. copy join: packets are the originals, byte for byte")

    # 3. re-encode
    out2 = os.path.join(d, "reencode.mp4")
    frames = concat_reencode(paths, out2, crf=17, preset="ultrafast")
    assert frames == total, frames
    cols2 = frame_colours(out2)
    assert len(cols2) == total, len(cols2)
    at = 0
    for n, rgb, _hz in specs:
        assert close(cols2[at], rgb), (at, cols2[at], rgb)
        at += n
    vdur2, adur2, n_audio2 = stream_stats(out2)
    assert abs(vdur2 - total / FPS) < 1.0 / FPS
    assert abs(adur2 - vdur2) < 0.05, (adur2, vdur2)
    print("3. re-encode join: %d frames, boundaries exact, video %.3fs "
          "audio %.3fs" % (total, vdur2, adur2))

    # 4. a clip without sound still joins by copy (video only)
    silent = os.path.join(d, "silent.mp4")
    o = av.open(silent, mode="w")
    vs = o.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    img = np.full((H, W, 3), 128, dtype=np.uint8)
    for _ in range(10):
        for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            o.mux(p)
    for p in vs.encode():
        o.mux(p)
    o.close()
    out3 = os.path.join(d, "silent_join.mp4")
    concat_copy([silent, silent], out3)
    assert len(frame_colours(out3)) == 20
    print("4. video-only clips join by copy")

    print("all checks passed")


if __name__ == "__main__":
    main()
