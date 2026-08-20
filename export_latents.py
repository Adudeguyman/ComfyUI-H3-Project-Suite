"""Assemble a master from the approved takes' saved latents.

The chain's whole design keeps generated footage out of pixel space
between clips; this does the same for delivery. Each approved take's
saved AV latent is decoded fresh, level matching is applied in float,
and every frame meets H.264 exactly once - at export, with settings
chosen for delivery. The per-clip MP4s stay what they always were:
review artifacts.

Memory stays bounded: one clip is decoded, written, and released before
the next is touched.

Two facts this module leans on:

- Project Save stores the FULL sampler latent, while the MP4 beside it
  is trimmed. The delivered length is in the sidecar meta, so each
  clip's decode keeps its LAST meta["frames"] frames (the re-tread sits
  at the head) and the matching tail of the audio. No trim setting needs
  recording; the pair is self-describing.
- The route has no VAEs. H3 Context registers the ones the graph is
  actually using each time it runs, and export borrows them. No render
  this session means no VAEs, and that is reported plainly rather than
  silently falling back.
"""

import json
import logging
import os

_LOG = logging.getLogger(__name__)

# the VAEs the running graph last used, registered by H3 Context
_VAES = {}


def register_vaes(video_vae, audio_vae):
    if video_vae is not None:
        _VAES["video"] = video_vae
    if audio_vae is not None:
        _VAES["audio"] = audio_vae


def vaes_ready():
    return "video" in _VAES


def _require():
    import av
    import numpy as np
    return av, np


def _frames_from_decode(out):
    """Whatever shape a video VAE hands back -> [T, H, W, 3] float 0..1."""
    import numpy as np
    t = out
    if hasattr(t, "detach"):
        t = t.detach().float().cpu().numpy()
    t = np.asarray(t, dtype=np.float32)
    if t.ndim == 5:                       # [B, T, H, W, C] or [B, C, T, H, W]
        t = t[0]
    if t.ndim != 4:
        raise RuntimeError("h3_suite: unexpected decode shape %s"
                           % (t.shape,))
    if t.shape[-1] not in (3, 4):         # channels-first -> channels-last
        t = np.moveaxis(t, 0 if t.shape[0] in (3, 4) else 1, -1)
    return t[..., :3]


def _waveform_from_decode(out):
    """Whatever an audio VAE hands back -> [C, S] float."""
    import numpy as np
    t = out
    if isinstance(t, dict):
        t = t.get("waveform", t.get("samples"))
    if hasattr(t, "detach"):
        t = t.detach().float().cpu().numpy()
    t = np.asarray(t, dtype=np.float32)
    while t.ndim > 2:
        t = t[0]
    if t.ndim == 1:
        t = t[None, :]
    return t


def _plan_from_arrays(prev_tail, next_head, np):
    """level_match.measure(), on float frames instead of files.

    prev_tail / next_head are [N, H, W, 3] in 0..255 float. Returns the
    same plan dict shape measure() produces, or None when the join needs
    nothing, so the thresholds stay in one place conceptually.
    """
    from .level_match import MAX_GAIN_DEV, MIN_STEP

    a_luma = float(np.mean(prev_tail))
    b_luma = float(np.mean(next_head[:len(prev_tail)]))
    step = b_luma - a_luma
    if abs(step) < MIN_STEP or b_luma <= 0.01:
        return None
    gain = a_luma / b_luma
    if abs(gain - 1.0) > MAX_GAIN_DEV:
        _LOG.warning("h3_suite: join step %+.1f is too large to level "
                     "match; leaving it alone", step)
        return None
    a_rgb = np.mean(prev_tail.reshape(-1, 3), axis=0)
    b_rgb = np.mean(next_head[:len(prev_tail)].reshape(-1, 3), axis=0)
    rgb_gain = np.where(b_rgb > 0.01, a_rgb / np.maximum(b_rgb, 1e-6), 1.0)
    rgb_gain = 1.0 + (rgb_gain - 1.0) * 0.5 + (gain - 1.0) * 0.5

    # decay fit, same maths as level_match._fit_decay on the head lumas
    vals = np.array([float(np.mean(f)) for f in next_head],
                    dtype=np.float64)
    excess = vals - a_luma
    tau = None
    if len(excess) >= 8 and excess[0] > 0.5:
        usable = []
        for i, e in enumerate(excess):
            if e <= max(0.3, excess[0] * 0.05):
                break
            usable.append((i, e))
        if len(usable) >= 5:
            idx = np.array([u[0] for u in usable], dtype=np.float64)
            logv = np.log(np.array([u[1] for u in usable],
                                   dtype=np.float64))
            slope, _icept = np.polyfit(idx, logv, 1)
            if slope < -1e-6:
                t = -1.0 / slope
                if 1.0 < t < len(next_head) * 4:
                    tau = float(t)
    span = int(min(len(next_head), tau * 4)) if tau else \
        min(36, len(next_head))
    return {"step": step, "gain": float(gain), "rgb_gain": rgb_gain,
            "tau": tau, "span": span}


def _apply_plan(frames, plan, np):
    """Fade the correction over the head, in place, in float."""
    if plan is None:
        return frames
    rgb_gain, span, tau = plan["rgb_gain"], plan["span"], plan["tau"]
    for i in range(min(span, len(frames))):
        w = float(np.exp(-i / tau)) if tau else 1.0 - (i / float(span))
        frames[i] = np.clip(frames[i] * (1.0 + (rgb_gain - 1.0) * w),
                            0, 255)
    return frames


def _clip_meta(project, basename):
    side = os.path.join(project.clips_dir, basename + ".json")
    try:
        with open(side, encoding="utf-8") as fh:
            return json.load(fh).get("meta") or {}
    except Exception:
        return {}


def export_from_latents(project, clips, master_path, level_match=True,
                        crf=17):
    """Decode approved takes one at a time into a single encode."""
    av, np = _require()
    try:
        from safetensors.torch import load_file as st_load
    except ImportError as exc:
        raise RuntimeError("h3_suite: safetensors unavailable (%s)" % exc)
    if "video" not in _VAES:
        raise RuntimeError(
            "h3_suite: no VAEs registered this session. Queue any clip "
            "once so H3 Context can register the graph's VAEs, or export "
            "from the clip videos instead.")
    vae = _VAES["video"]
    audio_vae = _VAES.get("audio")

    # every latent must exist BEFORE the first frame is written: a master
    # that silently swapped one clip to its MP4 would misrepresent itself
    missing = [c["basename"] for c in clips
               if not os.path.isfile(os.path.join(
                   project.clips_dir, c["basename"] + ".safetensors"))]
    if missing:
        raise RuntimeError(
            "h3_suite: latents missing for %s. Export from the clip "
            "videos instead, or re-render those clips."
            % ", ".join(missing))

    out = av.open(master_path, mode="w",
                  options={"movflags": "+faststart"})
    vs = None
    aso = None
    sample_rate = None
    prev_tail = None
    matched = []
    fps = 24

    try:
        for c in clips:
            basename = c["basename"]
            meta = _clip_meta(project, basename)
            fps = int(meta.get("fps") or fps)
            tensors = st_load(os.path.join(project.clips_dir,
                                           basename + ".safetensors"))
            frames = _frames_from_decode(vae.decode(tensors["video"]))
            deliver = int(meta.get("frames") or len(frames))
            # the saved latent is the FULL render; delivery keeps the tail
            frames = frames[len(frames) - deliver:]
            frames = np.ascontiguousarray(frames * 255.0
                                          if frames.max() <= 1.5
                                          else frames).astype(np.float32)

            if vs is None:
                vs = out.add_stream("libx264", rate=fps)
                vs.width = int(frames.shape[2])
                vs.height = int(frames.shape[1])
                vs.pix_fmt = "yuv420p"
                vs.options = {"crf": str(crf)}

            plan = None
            if level_match and prev_tail is not None:
                plan = _plan_from_arrays(prev_tail,
                                         frames[:144].copy(), np)
                if plan is not None:
                    frames = _apply_plan(frames, plan, np)
                    matched.append(c.get("index"))
            prev_tail = frames[-3:].copy()

            for f in frames:
                vf = av.VideoFrame.from_ndarray(
                    np.clip(f, 0, 255).astype(np.uint8), format="rgb24")
                for pkt in vs.encode(vf):
                    out.mux(pkt)
            del frames

            if audio_vae is not None and "audio" in tensors:
                wave = _waveform_from_decode(
                    audio_vae.decode(tensors["audio"]))
                sr = int(meta.get("sample_rate")
                         or getattr(audio_vae, "audio_sample_rate", 44100))
                keep = int(round(deliver / float(fps) * sr))
                wave = wave[:, max(0, wave.shape[1] - keep):]
                if aso is None:
                    # aac only accepts standard rates; resample linearly
                    # to 48k for anything unusual rather than refusing
                    _AAC_OK = (8000, 11025, 12000, 16000, 22050, 24000,
                               32000, 44100, 48000, 64000, 88200, 96000)
                    sample_rate = sr if sr in _AAC_OK else 48000
                    aso = out.add_stream("aac", rate=sample_rate)
                    aso.layout = "stereo"
                if sample_rate != sr and wave.shape[1] > 1:
                    n = int(round(wave.shape[1] * sample_rate / float(sr)))
                    xi = np.linspace(0, wave.shape[1] - 1, n)
                    wave = np.stack([np.interp(xi, np.arange(wave.shape[1]),
                                               ch) for ch in wave])
                if wave.shape[0] == 1:
                    wave = np.repeat(wave, 2, axis=0)
                af = av.AudioFrame.from_ndarray(
                    np.clip(wave[:2], -1, 1).astype(np.float32),
                    format="fltp", layout="stereo")
                af.sample_rate = sample_rate
                af.pts = None
                for pkt in aso.encode(af):
                    out.mux(pkt)
                del wave

        for pkt in vs.encode():
            out.mux(pkt)
        if aso is not None:
            for pkt in aso.encode():
                out.mux(pkt)
    finally:
        out.close()
    _LOG.info("h3_suite: master assembled from %d latents%s -> %s",
              len(clips),
              (", level matched joins %s" % matched) if matched else "",
              os.path.basename(master_path))
    return {"clips": len(clips), "level_matched": matched}
