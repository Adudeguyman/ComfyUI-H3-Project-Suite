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


def _classify(vae):
    """video or audio, from the loaded object rather than its filename.

    ComfyUI gives the H3 pair different shapes: the video VAE is 24
    latent channels over 3 dims, the audio VAE is 32 over 2 and carries
    a sample rate. Reading those is exact, where a filename heuristic
    would eventually pick the wrong decoder and produce garbage instead
    of an error.
    """
    ch = int(getattr(vae, "latent_channels", 0) or 0)
    dim = int(getattr(vae, "latent_dim", 0) or 0)
    if ch == 24 and dim == 3:
        return "video"
    if ch == 32 and dim == 2:
        return "audio"
    return None


def _vae_names_from_workflow(project, basename):
    """Every VAE filename the take's own workflow loaded."""
    side = os.path.join(project.clips_dir, basename + ".json")
    try:
        with open(side, encoding="utf-8") as fh:
            wf = json.load(fh).get("workflow") or {}
    except Exception:
        return []
    names = []
    for node in (wf.get("nodes") or []):
        if node.get("type") not in ("VAELoader", "VAELoaderNF4"):
            continue
        for v in (node.get("widgets_values") or []):
            if isinstance(v, str) and v.strip():
                names.append(v)
    return names


def load_vaes_for(project, clips, names=None):
    """Load the VAEs a chain was rendered with, without a render.

    Registration from H3 Context only happens when that node executes,
    which would make exporting depend on having generated something this
    session. The take's sidecar records the workflow that produced it,
    so the filenames are already on disk; this loads them the same way
    ComfyUI would and classifies each by what it turns out to be.
    """
    if not names and "video" in _VAES:
        return dict(_VAES)
    import comfy.sd
    import comfy.utils
    import folder_paths

    # names given by the caller win: the panel reads them straight off
    # the loaders wired into the Hub, which is the least guessy source
    # there is and needs nothing to have run
    seen = [n for n in (names or []) if n]
    for c in clips:
        for n in _vae_names_from_workflow(project, c["basename"]):
            if n not in seen:
                seen.append(n)
    if not seen:
        raise RuntimeError(
            "h3_suite: no VAE files to work with. Wire the H3 video and "
            "audio VAEs into the Project Hub node's vae and audio_vae "
            "inputs - the panel reads them straight from the loaders, "
            "so nothing needs to run first.")

    # only names ComfyUI itself lists for the vae folder are opened. The
    # list comes from the panel or a sidecar, neither of which gets to
    # point the loader at an arbitrary file just because it exists
    try:
        listed = set(folder_paths.get_filename_list("vae"))
    except Exception:
        listed = set()
    found = {}
    problems = []
    for name in seen:
        if len(found) == 2:
            break
        try:
            if name not in listed:
                problems.append("%s: not a file ComfyUI lists in the vae "
                                "folder" % name)
                continue
            path = folder_paths.get_full_path("vae", name)
            if not path:
                problems.append("%s: not found in the vae folder" % name)
                continue
            vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(path))
            kind = _classify(vae)
            if kind and kind not in found:
                found[kind] = vae
                _LOG.info("h3_suite: export loaded the %s VAE from %s",
                          kind, name)
        except Exception as exc:
            problems.append("%s: %s" % (name, exc))
    if "video" not in found:
        hint = ""
        if not any(n.lower().endswith((".safetensors", ".sft", ".ckpt",
                                       ".pt", ".pth", ".bin", ".gguf"))
                   for n in seen):
            hint = (" None of those is a model file: the Hub's vae inputs "
                    "are probably wired through a switch, reroute or "
                    "Get node the panel could not see past. Wire the "
                    "VAE loaders in directly, or name the files here.")
        raise RuntimeError(
            "h3_suite: could not load a video VAE. Tried %s.%s%s"
            % (", ".join(seen),
               (" Errors: " + "; ".join(problems)) if problems else "",
               hint))
    return found


def _require():
    import av
    import numpy as np
    return av, np


# the sample rates the aac encoder accepts
_AAC_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000,
              64000, 88200, 96000)


def _inference():
    """No autograd for model work that runs outside the executor.

    ComfyUI runs every node under torch.inference_mode(). A route handler
    inherits nothing of the sort, and a VAE decode with autograd live
    keeps every layer's activations for a backward pass that never
    comes: the whole GPU fills within a second on a clip the graph
    decodes in a few, with a peak two orders of magnitude higher. Same
    context here.

    torch is looked up, not imported: a real VAE cannot exist without it
    already loaded, and importing it here would drag a few hundred MB
    into a process that only fakes the decode (the memory probe).
    """
    import contextlib
    import sys
    torch = sys.modules.get("torch")
    if torch is None or not hasattr(torch, "inference_mode"):
        return contextlib.nullcontext()
    return torch.inference_mode()


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
    """Whatever an audio VAE hands back -> [C, S] float, levelled the way
    ComfyUI's own audio decode node levels it.

    Core's VAE.decode returns audio channels-LAST ([B, S, C]); its decode
    node swaps that to [B, C, S] and then divides by five standard
    deviations (never boosting). The review clips were written from that
    node's output, so the master applies the same two steps, or its
    sound would be a transposed handful of samples and, once fixed, a
    different loudness from what was reviewed.
    """
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
    if t.shape[0] > 8 >= t.shape[1]:      # [S, C] -> [C, S]
        t = np.ascontiguousarray(t.T)
    std = float(t.std()) * 5.0
    if std > 1.0:
        t = t / std
    return t


def _frame_stats(frames, count, scale, np):
    """Per-frame mean luma and mean rgb, without a scaled copy.

    Level matching only ever needed a handful of numbers per frame. The
    first version scaled whole slices to get them, which for a long clip
    is gigabytes of temporaries to compute a few hundred floats.
    """
    lumas, rgbs = [], []
    for i in range(min(count, len(frames))):
        f = frames[i]
        lumas.append(float(f.mean()) * scale)
        rgbs.append(np.asarray(f.reshape(-1, 3).mean(axis=0),
                               dtype=np.float64) * scale)
    return lumas, rgbs


def _plan_from_stats(prev_luma, prev_rgb, head_lumas, head_rgbs, np):
    """level_match.measure(), from statistics instead of pixels."""
    from .level_match import MAX_GAIN_DEV, MIN_STEP

    if not head_lumas:
        return None
    n = min(len(prev_rgb) if hasattr(prev_rgb, "__len__") else 3,
            len(head_lumas))
    b_luma = float(np.mean(head_lumas[:max(1, min(3, len(head_lumas)))]))
    step = b_luma - prev_luma
    if abs(step) < MIN_STEP or b_luma <= 0.01:
        return None
    gain = prev_luma / b_luma
    if abs(gain - 1.0) > MAX_GAIN_DEV:
        _LOG.warning("h3_suite: join step %+.1f is too large to level "
                     "match; leaving it alone", step)
        return None
    b_rgb = np.mean(np.stack(head_rgbs[:max(1, min(3, len(head_rgbs)))]),
                    axis=0)
    rgb_gain = np.where(b_rgb > 0.01,
                        np.asarray(prev_rgb) / np.maximum(b_rgb, 1e-6), 1.0)
    rgb_gain = 1.0 + (rgb_gain - 1.0) * 0.5 + (gain - 1.0) * 0.5

    vals = np.asarray(head_lumas, dtype=np.float64)
    excess = vals - prev_luma
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
                if 1.0 < t < len(head_lumas) * 4:
                    tau = float(t)
    span = int(min(len(head_lumas), tau * 4)) if tau else \
        min(36, len(head_lumas))
    return {"step": step, "gain": float(gain), "rgb_gain": rgb_gain,
            "tau": tau, "span": span}


def _apply_plan_frame(frame, i, plan, np):
    """Fade the correction across the head, one frame at a time."""
    if plan is None or i >= plan["span"]:
        return frame
    tau, span = plan["tau"], plan["span"]
    w = float(np.exp(-i / tau)) if tau else 1.0 - (i / float(span))
    frame *= (1.0 + (plan["rgb_gain"] - 1.0) * w)
    return frame


def _clip_meta(project, basename):
    side = os.path.join(project.clips_dir, basename + ".json")
    try:
        with open(side, encoding="utf-8") as fh:
            return json.load(fh).get("meta") or {}
    except Exception:
        return {}


def export_from_latents(project, clips, master_path, level_match=True,
                        crf=16, preset="medium", vae_names=None):
    """Decode approved takes one at a time into a single encode."""
    av, np = _require()
    try:
        from safetensors.torch import load_file as st_load
    except ImportError as exc:
        raise RuntimeError("h3_suite: safetensors unavailable (%s)" % exc)
    # the running graph's VAEs when a clip has been queued this session,
    # otherwise loaded from the takes' own recorded workflow - exporting
    # must not require having generated something first
    ready = load_vaes_for(project, clips, names=vae_names)
    vae = ready["video"]
    audio_vae = ready.get("audio")

    # one picture size, checked before anything is decoded: the encoder
    # is opened at the first clip's size and would fail on the odd one
    from .project import check_uniform_size
    check_uniform_size(clips)

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
    prev_stats = None
    matched = []
    fps = 24
    abuf = None          # sound waiting to be emitted in whole frames
    audio_pts = 0        # running sample position on the output stream

    def _encode_audio(seg, pts):
        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(seg),
                                        format="fltp", layout="stereo")
        af.sample_rate = sample_rate
        af.pts = pts
        for pkt in aso.encode(af):
            out.mux(pkt)
        return pts + int(seg.shape[1])

    try:
        for c in clips:
            basename = c["basename"]
            meta = _clip_meta(project, basename)
            fps = int(meta.get("fps") or fps)
            tensors = st_load(os.path.join(project.clips_dir,
                                           basename + ".safetensors"))
            with _inference():
                frames = _frames_from_decode(vae.decode(tensors["video"]))
            deliver = int(meta.get("frames") or len(frames))
            # the saved latent is the FULL render; delivery keeps the tail
            frames = frames[len(frames) - deliver:]
            # NO whole-clip conversion: a 13 second 928x928 clip is 3 GB
            # of frames, and scaling it as a batch made three more. Each
            # frame is scaled, corrected and encoded on its own below, so
            # peak memory is one frame on top of the decode.
            scale = 255.0 if float(frames.max()) <= 1.5 else 1.0

            if vs is None:
                vs = out.add_stream("libx264", rate=fps)
                vs.width = int(frames.shape[2])
                vs.height = int(frames.shape[1])
                vs.pix_fmt = "yuv420p"
                # the master is the deliverable, so it gets its own
                # settings rather than inheriting the review clips'
                vs.options = {"crf": str(int(crf)), "preset": str(preset)}
                # every stream must exist before the first packet is
                # muxed: the header goes out with that packet, and a
                # stream added afterwards never gets a time base, so
                # its packets cannot be written. The sample rate is known
                # from the sidecar (or the VAE) without decoding anything.
                if audio_vae is not None and "audio" in tensors:
                    sr0 = int(meta.get("sample_rate")
                              or getattr(audio_vae, "audio_sample_rate",
                                         44100))
                    # aac only accepts standard rates; anything unusual
                    # is resampled linearly to 48k rather than refused
                    sample_rate = sr0 if sr0 in _AAC_RATES else 48000
                    aso = out.add_stream("aac", rate=sample_rate)
                    aso.layout = "stereo"

            plan = None
            if level_match and prev_stats is not None:
                head_lumas, head_rgbs = _frame_stats(frames, 144, scale, np)
                plan = _plan_from_stats(prev_stats[0], prev_stats[1],
                                        head_lumas, head_rgbs, np)
                if plan is not None:
                    matched.append(c.get("index"))

            for i in range(len(frames)):
                f = np.asarray(frames[i], dtype=np.float32) * scale
                f = _apply_plan_frame(f, i, plan, np)
                np.clip(f, 0, 255, out=f)
                vf = av.VideoFrame.from_ndarray(
                    np.ascontiguousarray(f, dtype=np.float32
                                         ).astype(np.uint8),
                    format="rgb24")
                for pkt in vs.encode(vf):
                    out.mux(pkt)

            # the tail this join will be matched against, as statistics
            tail_l, tail_rgb = _frame_stats(frames[-3:], 3, scale, np)
            prev_stats = (float(np.mean(tail_l)),
                          np.mean(np.stack(tail_rgb), axis=0))
            del frames

            if audio_vae is not None and "audio" in tensors and aso is None:
                # the first clip had no sound, so the master has no
                # audio stream and none can be added now (see above)
                _LOG.warning("h3_suite: %s has sound but the master's "
                             "first clip did not; its sound is left out",
                             basename)
            elif audio_vae is not None and "audio" in tensors:
                with _inference():
                    wave = _waveform_from_decode(
                        audio_vae.decode(tensors["audio"]))
                sr = int(meta.get("sample_rate")
                         or getattr(audio_vae, "audio_sample_rate", 44100))
                keep = int(round(deliver / float(fps) * sr))
                wave = wave[:, max(0, wave.shape[1] - keep):]
                if sample_rate != sr and wave.shape[1] > 1:
                    n = int(round(wave.shape[1] * sample_rate / float(sr)))
                    xi = np.linspace(0, wave.shape[1] - 1, n)
                    wave = np.stack([np.interp(xi, np.arange(wave.shape[1]),
                                               ch) for ch in wave])
                if wave.shape[0] == 1:
                    wave = np.repeat(wave, 2, axis=0)
                # the encoder wants 1024-sample frames with explicit,
                # running timestamps (PyAV no longer fills them in), so
                # the clip's sound joins a buffer that is emitted in
                # whole frames; the remainder carries over to the next
                # clip and only the final one may be short
                wave = np.ascontiguousarray(np.clip(wave[:2], -1, 1),
                                            dtype=np.float32)
                abuf = wave if abuf is None else np.concatenate(
                    (abuf, wave), axis=1)
                del wave
                while abuf.shape[1] >= 1024:
                    seg, abuf = abuf[:, :1024], abuf[:, 1024:]
                    audio_pts = _encode_audio(seg, audio_pts)

        for pkt in vs.encode():
            out.mux(pkt)
        if aso is not None:
            if abuf is not None and abuf.shape[1]:
                _encode_audio(abuf, audio_pts)
            for pkt in aso.encode():
                out.mux(pkt)
    finally:
        out.close()
    _LOG.info("h3_suite: master assembled from %d latents%s -> %s",
              len(clips),
              (", level matched joins %s" % matched) if matched else "",
              os.path.basename(master_path))
    return {"clips": len(clips), "level_matched": matched}
