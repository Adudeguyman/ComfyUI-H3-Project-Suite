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

import logging

_LOG = logging.getLogger(__name__)

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
