"""Conform arbitrary footage onto H3's grids, and say what it cost.

Bringing outside video into a chain is mostly arithmetic, and every part
of it can silently ruin a join:

- H3 runs at 24 fps. Source footage rarely does. Frames are remapped by
  INDEX onto 24 fps centres rather than interpolated: selection invents
  nothing, where a blend would hand the model pixels no camera saw.
- A clip's length has to sit on H3's ladder - 17m + 5 frames - so the
  last latent step is whole. Anything else gets trimmed.
- Audio latents run at 40 Hz against video's 24, so a frame count maps
  to a fractional number of audio steps. The waveform is conformed to
  the exact length the video implies, with a guard rail: a stretch
  beyond half a percent means something is wrong with the input, and
  drifting sync silently is worse than refusing.

Nothing here decides for the person. The node reports what it kept,
what it dropped and from which end, and the imported clip lands in the
project as clip 1 for review before anything continues from it.
"""

import json
import logging
import os

_LOG = logging.getLogger(__name__)


def json_dumps(obj, indent=None):
    return json.dumps(obj, separators=(",", ":")
                      if indent is None else None,
                      indent=indent)

FPS = 24
FRAME_PER_TOKEN = (1, 4, 4, 4, 4)
LADDER_STEP = 17          # valid lengths are 17m + 5
LADDER_BASE = 5
AUDIO_HZ = 40.0
MAX_AUDIO_STRETCH = 0.005


def valid_lengths(upto):
    """Every frame count H3 can render, up to `upto`."""
    out, n = [], LADDER_BASE
    while n <= upto:
        out.append(n)
        n += LADDER_STEP
    return out


def snap_length(n):
    """Largest valid length that fits in n frames, or 0 if none does."""
    if n < LADDER_BASE:
        return 0
    return LADDER_BASE + ((n - LADDER_BASE) // LADDER_STEP) * LADDER_STEP


def cfr_index_map(count, source_fps, target_fps=FPS):
    """Source frame index for each target-fps frame centre.

    Selection, not interpolation: every output frame is a real source
    frame, so nothing is invented and nothing is blended.
    """
    source_fps = float(source_fps)
    if source_fps <= 0:
        raise ValueError("h3_suite: source_fps must be greater than 0.")
    if count <= 0:
        raise ValueError("h3_suite: the source has no frames.")
    if abs(source_fps - target_fps) < 1e-6:
        return list(range(count))
    out_n = max(1, int(round(count * float(target_fps) / source_fps)))
    idx = []
    for i in range(out_n):
        t = (i + 0.5) / float(target_fps)
        src = int(round(t * source_fps - 0.5))
        idx.append(min(max(src, 0), count - 1))
    return idx


def plan(count, source_fps, align="tail"):
    """What conforming would do, without doing it.

    align picks which end survives when frames have to go: "tail" keeps
    the end (the natural choice when the footage runs INTO the chain),
    "head" keeps the beginning, "center" trims both ends evenly.
    """
    idx = cfr_index_map(count, source_fps)
    resampled = len(idx)
    keep = snap_length(resampled)
    if keep <= 0:
        raise ValueError(
            "h3_suite: after conforming to %d fps there are %d frames, "
            "fewer than the %d H3 needs for one latent step."
            % (FPS, resampled, LADDER_BASE))
    drop = resampled - keep
    if align == "head":
        start = 0
    elif align == "center":
        start = drop // 2
    else:
        start = drop
    return {
        "source_frames": count,
        "source_fps": float(source_fps),
        "resampled": resampled,
        "keep": keep,
        "drop": drop,
        "start": start,
        "end": start + keep,
        "seconds": keep / float(FPS),
        "dropped_seconds": drop / float(FPS),
        "align": align,
        "steps": (keep - LADDER_BASE) // LADDER_STEP * 5 + 2,
    }


def describe(p):
    """The report a person reads before approving the import."""
    lines = []
    if abs(p["source_fps"] - FPS) < 1e-6:
        lines.append("%d frames at %g fps, already H3's rate."
                     % (p["source_frames"], p["source_fps"]))
    else:
        lines.append("%d frames at %g fps -> %d frames at %d fps "
                     "(picked by index, nothing blended)."
                     % (p["source_frames"], p["source_fps"],
                        p["resampled"], FPS))
    if p["drop"]:
        where = {"head": "from the start", "tail": "from the end",
                 "center": "from both ends"}[p["align"]]
        lines.append(
            "Trimmed %d frame%s (%.2fs) %s to reach %d, the nearest "
            "length H3 can render. Keeping frames %d-%d."
            % (p["drop"], "" if p["drop"] == 1 else "s",
               p["dropped_seconds"], where, p["keep"],
               p["start"], p["end"] - 1))
    else:
        lines.append("No trim needed: %d frames is already on H3's grid."
                     % p["keep"])
    lines.append("Result: %d frames, %.2fs, %d latent steps."
                 % (p["keep"], p["seconds"], p["steps"]))
    nearest = [n for n in valid_lengths(p["resampled"] + LADDER_STEP * 2)]
    near = [n for n in nearest if abs(n - p["resampled"]) <= LADDER_STEP]
    if p["drop"] and near:
        lines.append("Nearby valid lengths: %s."
                     % ", ".join(str(n) for n in near))
    return "\n".join(lines)


def conform_audio(waveform, sample_rate, vae_sample_rate, frames,
                  resample):
    """Match the waveform to what `frames` at 24 fps implies.

    Returns (waveform, note). The guard rail matters: video and audio
    grids are 24 and 40, so a frame count lands between audio steps and
    a small correction is normal. A large one means the audio does not
    belong to this video, and stretching it anyway would drift sync
    across the whole chain.
    """
    notes = []
    if int(sample_rate) != int(vae_sample_rate):
        waveform = resample(waveform, int(sample_rate),
                            int(vae_sample_rate))
        notes.append("resampled audio %d -> %d Hz"
                     % (sample_rate, vae_sample_rate))
    want = int(round(frames / float(FPS) * vae_sample_rate))
    have = int(waveform.shape[-1])
    if have == 0:
        raise ValueError("h3_suite: the source audio is empty.")
    change = abs(have - want) / float(want)
    if change > MAX_AUDIO_STRETCH and have < want:
        raise ValueError(
            "h3_suite: the audio is %.2fs but the trimmed video is "
            "%.2fs. That is more than a grid rounding, so this audio "
            "probably is not this video's. Trim them to match, or leave "
            "audio unwired."
            % (have / float(vae_sample_rate), want / float(vae_sample_rate)))
    if have > want:
        waveform = waveform[..., have - want:]
        notes.append("took the last %.2fs of audio to match the kept video"
                     % (want / float(vae_sample_rate)))
    elif have < want:
        pad = want - have
        import torch
        waveform = torch.nn.functional.pad(waveform, (0, pad))
        notes.append("padded %d samples (%.3fs) of silence to reach the "
                     "video length" % (pad, pad / float(vae_sample_rate)))
    return waveform, "; ".join(notes)


# ---------------------------------------------------------------------
# Reading a file, and importing a chosen window from it.
#
# The panel picks the window against real frames, so these read the
# container directly rather than trusting a node input. A file's
# declared rate can be a fiction (variable frame rate, or a container
# average), so the count is verified rather than derived.
# ---------------------------------------------------------------------


def probe_video(path):
    """Frame count, rate and size, read from the file itself."""
    import av
    with av.open(path) as c:
        if not c.streams.video:
            raise RuntimeError("h3_suite: %s has no video stream."
                               % path.rsplit("/", 1)[-1])
        vs = c.streams.video[0]
        fps = float(vs.average_rate or vs.guessed_rate or FPS)
        width, height = int(vs.codec_context.width), \
            int(vs.codec_context.height)
        frames = int(vs.frames or 0)
        duration = float(vs.duration * vs.time_base) if vs.duration else 0.0
        has_audio = bool(c.streams.audio)
    if frames <= 0:
        # some containers do not record it; count without decoding pixels
        with av.open(path) as c:
            frames = sum(1 for _ in c.demux(video=0) if _.pts is not None)
    resampled = len(cfr_index_map(frames, fps)) if frames else 0
    return {
        "frames": frames, "fps": fps, "width": width, "height": height,
        "duration": duration or (frames / fps if fps else 0.0),
        "has_audio": has_audio,
        # what the panel scrubs against: positions on H3's 24 fps timeline
        "resampled": resampled,
        "valid_lengths": valid_lengths(resampled),
    }


def read_window(path, start, frames, source_fps=None):
    """Decode exactly the 24 fps window the panel chose.

    start and frames are indices on the RESAMPLED timeline, which is
    what the filmstrip shows, so what is decoded here is precisely what
    was previewed.
    """
    import av
    import numpy as np

    info = probe_video(path)
    fps = float(source_fps or info["fps"])
    idx = cfr_index_map(info["frames"], fps)
    picked = idx[start:start + frames]
    if not picked:
        raise RuntimeError("h3_suite: that window contains no frames.")
    wanted = sorted(set(picked))
    grabbed = {}
    with av.open(path) as c:
        stream = c.streams.video[0]
        stream.thread_type = "AUTO"
        seen = 0
        target = set(wanted)
        last = wanted[-1]
        for frame in c.decode(video=0):
            if seen in target:
                grabbed[seen] = frame.to_ndarray(format="rgb24")
            seen += 1
            if seen > last:
                break
    missing = [i for i in wanted if i not in grabbed]
    if missing:
        raise RuntimeError(
            "h3_suite: could not read %d frame(s) from the source; the "
            "file may be truncated." % len(missing))
    arr = np.stack([grabbed[i] for i in picked]).astype("float32") / 255.0
    return arr, info


def read_audio(path, start, frames, source_fps=None, target_sr=32000):
    """The waveform under the chosen window, at the VAE's rate."""
    import av
    import numpy as np

    info = probe_video(path)
    if not info["has_audio"]:
        return None
    fps = float(source_fps or info["fps"])
    t0 = start / float(FPS)
    t1 = (start + frames) / float(FPS)
    chunks = []
    with av.open(path) as c:
        astream = c.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout="stereo", rate=target_sr)
        for frame in c.decode(audio=0):
            ts = float(frame.pts * astream.time_base) if frame.pts else 0.0
            if ts > t1:
                break
            for out in resampler.resample(frame):
                chunks.append((ts, out.to_ndarray()))
    if not chunks:
        return None
    wave = np.concatenate([c[1] for c in chunks], axis=-1)
    if wave.ndim == 1:
        wave = wave[None, :]
    a = int(max(0, round(t0 * target_sr)))
    b = int(round(t1 * target_sr))
    return wave[:, a:b]


def snap_size(width, height):
    """The largest H3-renderable size at or below the given one."""
    # imported here, not at module scope: every relative import in this
    # file is function-local so the probes can load it on its own
    from .project import SIZE_STEP
    w = max(SIZE_STEP, int(width) // SIZE_STEP * SIZE_STEP)
    h = max(SIZE_STEP, int(height) // SIZE_STEP * SIZE_STEP)
    return w, h


def target_size(project, src_width, src_height, want_width=0,
                want_height=0):
    """(width, height, why) an import into this project must be encoded at.

    A project with clips already has a picture size, and an import must
    match it or the next clip cannot continue from it and the master
    cannot join it: 'project'. An empty project is sized by the import,
    from the requested size if one was given, else from the footage,
    snapped to what H3 can render: 'source'.
    """
    res = project.resolution()
    if res:
        return res[0], res[1], "project"
    if want_width and want_height:
        w, h = snap_size(want_width, want_height)
    else:
        w, h = snap_size(src_width, src_height)
    return w, h, "source"


def conform_frames(images, target_w, target_h, fit="fill", offset=0.5):
    """[T,H,W,C] footage -> exactly target_w x target_h.

    fit="fill" keeps the whole frame height (or width) and drops the
    other axis to reach the target shape; `offset` says where that
    window sits, 0 at the top or left, 1 at the bottom or right, 0.5
    centred - the panel's draggable box sends the value it showed you.
    fit="fit" keeps the entire frame and adds bars instead.

    The crop is a slice of real pixels; only the scale interpolates.
    """
    import torch

    from .nodes import _resize

    src_h, src_w = int(images.shape[1]), int(images.shape[2])
    if (src_w, src_h) == (target_w, target_h):
        return images
    offset = min(max(float(offset), 0.0), 1.0)
    src_aspect = src_w / float(src_h)
    tgt_aspect = target_w / float(target_h)

    if fit == "fit":
        # the whole frame, scaled to fit inside the target, bars around it
        scale = min(target_w / float(src_w), target_h / float(src_h))
        inner_w = max(1, int(round(src_w * scale)) // 2 * 2)
        inner_h = max(1, int(round(src_h * scale)) // 2 * 2)
        inner = _resize(images, inner_w, inner_h, "disabled")
        out = torch.zeros((images.shape[0], target_h, target_w,
                           inner.shape[-1]), dtype=inner.dtype,
                          device=inner.device)
        x = (target_w - inner_w) // 2
        y = (target_h - inner_h) // 2
        out[:, y:y + inner_h, x:x + inner_w, :] = inner
        return out

    if abs(src_aspect - tgt_aspect) > 1e-3:
        if src_aspect > tgt_aspect:          # too wide: take a column
            keep_w = max(1, int(round(src_h * tgt_aspect)))
            x = int(round((src_w - keep_w) * offset))
            images = images[:, :, x:x + keep_w, :]
        else:                                 # too tall: take a band
            keep_h = max(1, int(round(src_w / tgt_aspect)))
            y = int(round((src_h - keep_h) * offset))
            images = images[:, y:y + keep_h, :, :]
    return _resize(images, target_w, target_h, "disabled")


def import_window(project, path, start, frames, width, height,
                  crop="center", with_audio=True, vae_names=None,
                  fit="fill", crop_offset=0.5):
    """Decode a window, encode it, and write it into the project.

    Everything the chain needs for a first clip: the AV latent, the
    video that latent contains, and a sidecar recording what the import
    did. The clip arrives PENDING, so the trim is reviewed in the panel
    like any other take.
    """
    import numpy as np
    import torch

    from .export_latents import load_vaes_for
    from .project import ProjectError

    if frames <= 0 or (frames - LADDER_BASE) % LADDER_STEP != 0:
        raise RuntimeError(
            "h3_suite: %d frames is not a length H3 can render. Valid "
            "lengths are 5, 22, 39, 56 and so on." % frames)

    vaes = load_vaes_for(project, project.clips, names=vae_names)
    vae = vaes["video"]
    audio_vae = vaes.get("audio")

    arr, info = read_window(path, start, frames)
    tw, th, why = target_size(project, info["width"], info["height"],
                              width, height)
    if why == "project" and width and height and (int(width), int(height)) != (tw, th):
        _LOG.info("h3_suite: import asked for %dx%d; the project is %dx%d "
                  "and every clip must match it", width, height, tw, th)
    # this runs from a route, not from the executor, so nothing has
    # switched autograd off for us; an encode with it on keeps every
    # layer's activations and fills the GPU (see export_latents)
    with torch.inference_mode():
        images = torch.from_numpy(arr)
        if (int(images.shape[2]), int(images.shape[1])) != (tw, th):
            images = conform_frames(images, tw, th, fit=fit,
                                    offset=crop_offset)
            _LOG.info("h3_suite: import %s %dx%d -> %dx%d (%s)",
                      "fitted with bars" if fit == "fit"
                      else "cropped at %d%% and scaled"
                           % round(float(crop_offset) * 100),
                      info["width"], info["height"], tw, th,
                      "to match the project" if why == "project"
                      else "sets the project size")

        parts = [vae.encode(images[..., :3])]
        audio_dict = None
        if with_audio and audio_vae is not None and info["has_audio"]:
            sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
            wave = read_audio(path, start, frames, target_sr=sr)
            if wave is not None:
                w = torch.from_numpy(np.ascontiguousarray(wave))[None]
                w, _note = conform_audio(w, sr, sr, frames,
                                         lambda x, a, b: x)
                audio_dict = {"waveform": w, "sample_rate": sr}
                # core's VAE.encode takes channels LAST ([B, S, C]) and
                # swaps to [B, C, S] itself; handing it ComfyUI's AUDIO
                # layout directly makes the DAC encoder see two samples
                # of S channels and fail on its first convolution
                parts.append(audio_vae.encode(w.movedim(1, -1)))

    # the two streams are saved side by side below, straight from parts.
    # There is deliberately no combined latent here: an H3 AV latent is a
    # NestedTensor, not a stack (the streams have different shapes), and
    # nothing in this function needs one.

    from .nodes import _st_save
    from .project_nodes import _write_video, _now_iso

    index, take, basename = project.next_save()
    video_lat = parts[0]
    audio_lat = parts[1] if len(parts) > 1 else None
    meta = {
        "width": int(images.shape[2]),
        "height": int(images.shape[1]),
        "frames": int(frames),
        "fps": FPS,
        "duration": round(frames / float(FPS), 4),
        "latent_video": list(getattr(video_lat, "shape", []) or []),
        "latent_audio": list(getattr(audio_lat, "shape", []) or [])
        if audio_lat is not None else [],
        "saved_at": _now_iso(),
        # what makes this clip's provenance readable later: it was not
        # rendered, and this is the exact window it came from
        "imported_from": os.path.basename(path),
        "imported_window": [int(start), int(start + frames)],
        "source_fps": round(float(info["fps"]), 4),
        "source_frames": int(info["frames"]),
    }
    if audio_dict is not None:
        meta["sample_rate"] = int(audio_dict["sample_rate"])

    latent_path = os.path.join(project.clips_dir,
                               basename + ".safetensors")
    _st_save({"video": video_lat,
              "audio": audio_lat if audio_lat is not None else video_lat},
             latent_path,
             {"format": "h3_motion_context_av_v1",
              "h3_project": project.name,
              "h3_meta": json_dumps(meta)})
    _write_video(os.path.join(project.clips_dir, basename + ".mp4"),
                 images, audio_dict, FPS,
                 {"comment": json_dumps({"project": project.name,
                                         "clip": basename,
                                         "meta": meta})})
    with open(os.path.join(project.clips_dir, basename + ".json"),
              "w", encoding="utf-8") as fh:
        fh.write(json_dumps({"meta": meta, "workflow": None}, indent=2))
    project.record_render(index, take, meta)
    _LOG.info("h3_suite: imported %s frames %d-%d as %s",
              os.path.basename(path), start, start + frames - 1, basename)
    return {"basename": basename, "index": index, "take": take,
            "frames": int(frames), "source_frames": int(info["frames"])}
