"""Join clip mp4s into one file with PyAV.

Two joins, matching what the export used to ask ffmpeg for:

  concat_copy      packets are copied straight through, so untouched
                   clips reach the master byte-identical. Every input
                   must share codec parameters, which the pack's own
                   writer guarantees.
  concat_reencode  frames are decoded and encoded once into a single
                   stream. Needed when any clip was level-matched, since
                   its corrected copy no longer shares parameters with
                   its neighbours.

Timestamps are rebased the way ffmpeg's concat demuxer does it: each
file starts where the previous file's container duration ended, one
offset for all its streams, so audio never drifts against picture over
a long chain. A packet whose decode timestamp would not advance past
the previous one on the same stream is nudged forward by one tick,
which is also what ffmpeg does at a join.
"""

import logging
import os
from fractions import Fraction

_LOG = logging.getLogger("h3_suite")

_MUX_OPTIONS = {"movflags": "+faststart"}


def _require():
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "h3_suite: PyAV is not available; cannot write the master. "
            "`pip install av` into the ComfyUI environment.") from exc
    return av


def _file_duration(container, streams):
    """Seconds this file occupies on the joined timeline."""
    if container.duration is not None and container.duration > 0:
        return Fraction(int(container.duration), 1_000_000)
    end = Fraction(0)
    for s in streams:
        if s.duration is not None and s.time_base is not None:
            end = max(end, Fraction(int(s.duration)) * s.time_base)
    return end


def concat_copy(paths, out_path):
    """Stream-copy join. Returns the number of inputs joined."""
    av = _require()
    out = av.open(out_path, mode="w", options=dict(_MUX_OPTIONS))
    out_streams = {}          # "video"/"audio" -> output stream
    last_dts = {}             # output stream index -> last dts in seconds
    offset = Fraction(0)
    try:
        for n, path in enumerate(paths):
            with av.open(path) as src:
                ins = []
                if src.streams.video:
                    ins.append(("video", src.streams.video[0]))
                if src.streams.audio:
                    ins.append(("audio", src.streams.audio[0]))
                if n == 0:
                    for kind, s in ins:
                        out_streams[kind] = out.add_stream_from_template(s)
                    for k, v in dict(src.metadata).items():
                        try:
                            out.metadata[k] = v
                        except Exception:
                            pass
                wanted = {s.index: kind for kind, s in ins
                          if kind in out_streams}
                for pkt in src.demux([s for _k, s in ins]):
                    if pkt.dts is None and pkt.pts is None:
                        continue                   # flush marker
                    kind = wanted.get(pkt.stream.index)
                    if kind is None:
                        continue
                    tb = pkt.time_base
                    shift = int(round(offset / tb))
                    if pkt.pts is not None:
                        pkt.pts += shift
                    if pkt.dts is not None:
                        pkt.dts += shift
                    os_ = out_streams[kind]
                    key = os_.index
                    if pkt.dts is not None:
                        prev = last_dts.get(key)
                        cur = Fraction(pkt.dts) * tb
                        if prev is not None and cur <= prev:
                            bump = int((prev - cur) / tb) + 1
                            pkt.dts += bump
                            if pkt.pts is not None and pkt.pts < pkt.dts:
                                pkt.pts = pkt.dts
                            cur = Fraction(pkt.dts) * tb
                        last_dts[key] = cur
                    pkt.stream = os_
                    out.mux(pkt)
                offset += _file_duration(src, [s for _k, s in ins])
    finally:
        out.close()
    _LOG.info("h3_suite: joined %d clip(s) by stream copy into %s",
              len(paths), os.path.basename(out_path))
    return len(paths)


def concat_reencode(paths, out_path, crf=17, preset="medium"):
    """Decode every clip and encode the join once. Returns frames written."""
    av = _require()
    import numpy as np

    with av.open(paths[0]) as first:
        v0 = first.streams.video[0]
        a0 = first.streams.audio[0] if first.streams.audio else None
        rate = v0.average_rate or v0.guessed_rate or Fraction(24, 1)
        width, height = v0.width, v0.height
        a_rate = int(a0.rate) if a0 is not None else None
        a_layout = (a0.layout.name if a0 is not None else None) or "stereo"
        metadata = dict(first.metadata)

    out = av.open(out_path, mode="w", options=dict(_MUX_OPTIONS))
    for k, v in metadata.items():
        try:
            out.metadata[k] = v
        except Exception:
            pass
    frames = 0
    try:
        vs = out.add_stream("libx264", rate=rate)
        vs.width, vs.height, vs.pix_fmt = width, height, "yuv420p"
        vs.options = {"crf": str(int(crf)), "preset": str(preset)}
        aso = None
        if a_rate:
            aso = out.add_stream("aac", rate=a_rate)
            aso.layout = a_layout

        # decoded frames carry their own file's timestamps; renumber them
        # on the joined timeline, one tick per frame at the output rate
        frame_tb = Fraction(1) / Fraction(rate)
        for path in paths:
            with av.open(path) as src:
                for frame in src.decode(video=0):
                    frame.pts = frames
                    frame.time_base = frame_tb
                    for p in vs.encode(frame):
                        out.mux(p)
                    frames += 1
        for p in vs.encode():
            out.mux(p)

        if aso is not None:
            # decoded aac comes in 1024-sample frames with a short one at
            # each file's end; the encoder wants exactly 1024 for every
            # frame but the last, so re-chunk through a buffer the way
            # the clip writer does
            chunk = 1024
            buf = None
            pts = 0
            to_fltp = None
            for path in paths:
                with av.open(path) as src:
                    if not src.streams.audio:
                        continue
                    for af in src.decode(audio=0):
                        if (af.format.name != "fltp" or int(af.sample_rate) != a_rate
                                or af.layout.name != a_layout):
                            if to_fltp is None:
                                to_fltp = av.AudioResampler(
                                    format="fltp", layout=a_layout, rate=a_rate)
                            parts = to_fltp.resample(af)
                        else:
                            parts = [af]
                        for part in parts:
                            arr = part.to_ndarray()
                            buf = arr if buf is None else np.concatenate(
                                (buf, arr), axis=1)
                            while buf.shape[1] >= chunk:
                                seg, buf = buf[:, :chunk], buf[:, chunk:]
                                fr = av.AudioFrame.from_ndarray(
                                    np.ascontiguousarray(seg), format="fltp",
                                    layout=a_layout)
                                fr.sample_rate = a_rate
                                fr.pts = pts
                                pts += chunk
                                for p in aso.encode(fr):
                                    out.mux(p)
            if buf is not None and buf.shape[1]:
                fr = av.AudioFrame.from_ndarray(
                    np.ascontiguousarray(buf), format="fltp", layout=a_layout)
                fr.sample_rate = a_rate
                fr.pts = pts
                for p in aso.encode(fr):
                    out.mux(p)
            for p in aso.encode():
                out.mux(p)
    finally:
        out.close()
    _LOG.info("h3_suite: re-encoded %d clip(s), %d frames, into %s",
              len(paths), frames, os.path.basename(out_path))
    return frames
