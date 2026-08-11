"""Pin previous-clip motion at the head of an H3 clip.

Wire it between a stock H3 conditioning node and the sampler:

    MiniMaxH3ImageToVideo / MiniMaxH3ReferenceToVideo (or the t2v path)
        -> H3 Context
        -> guider / sampler

Two axes to test, both cheap.

encode_mode
  frames  one VAE call per frame, each pinned as its own cond block. The
          model sees N snapshots at N instants.
  video   one VAE call for the whole run. The H3 video VAE has latent_dim
          3, so it reads the batch axis as time and compresses the run
          into fewer latent steps (5 pixel frames -> 2 steps, 22 -> 7).
          Each step becomes one cond block, so the motion between frames
          lives inside the latent instead of being implied across separate
          stills. Far fewer rows and one VAE load.

anchor_mode
  head    pinned frames occupy indices 0..N-1 of the delivered timeline.
          They come back in the output, so trim that many frames off the
          front before concatenating.
  before  pinned frames sit at negative indices, ending at -1, so
          delivered frame 0 continues from them and nothing is wasted.
          Their time coordinates land below text_len, which is the range
          the text rows occupy. Whether that collision matters is exactly
          what this mode is asking.
"""

import logging
import os

import comfy.utils
import folder_paths
import node_helpers
import torch

try:
    from safetensors.torch import load_file as _st_load, save_file as _st_save
except ImportError:  # ComfyUI always ships safetensors; belt and braces
    _st_load = _st_save = None

from .patch_layout import (
    MC_KEY,
    MC_AUDIO_KEY,
    apply_patch as apply_layout_patch,
    is_applied,
)
from .patch_payload import (
    apply_patch as apply_payload_patch,
    is_applied as payload_patch_applied,
)

try:
    import torchaudio
except ImportError:
    torchaudio = None

_LOG = logging.getLogger("h3_suite")


def _activate_inline_patches():
    """Opt this execution path into the two marker-gated H3 patches.

    Importing the pack only registers nodes; nothing in ComfyUI changes
    until an H3 Context node actually runs. Both wrappers call the original
    implementation unchanged for unmarked graphs, so stock H3 workflows
    queued after an opted-in one in the same process keep stock behavior.
    """
    layout_ok = apply_layout_patch()
    payload_ok = apply_payload_patch()
    if not layout_ok or not is_applied():
        raise RuntimeError(
            "h3_suite: the inline layout patch could not be enabled, so "
            "interior anchors would be rejected by ComfyUI. Check the log "
            "for the self-test failure reason.")
    if not payload_ok or not payload_patch_applied():
        raise RuntimeError(
            "h3_suite: the inline payload patch could not be enabled. "
            "Ref2VA refs could overwrite the pinned video latents; refusing "
            "to render a silently wrong clip.")

FRAME_PER_TOKEN = (1, 4, 4, 4, 4)
FPS = 24  # H3's native rate; audio latents run at 40 Hz, hence FRAME_RESCALE 5/3
FRAME_RESCALE = 5.0 / 3.0
AUDIO_HZ = 40.0

# Run lengths the video VAE's downscale formula max(1, (n - 5) // 17 * 5 + 2)
# actually distinguishes. Anything between two grid points encodes to the same
# number of latent steps as the lower one, but the steps then cover the FIRST
# `covered` frames of the input rather than the last: encoding 10 frames yields
# the same 2 steps as encoding 5, representing frames [-10..-6] of the source
# clip instead of [-5..-1]. The pinned run would end five frames early and the
# delivered clip would continue from the wrong instant. So off-grid requests
# are snapped DOWN before slicing, keeping content and coverage in agreement.
# every 17m+5 length phase-aligns on the latent path (its step count
# is 2 mod 5); the node's input caps at 56 like upstream, but the
# snap-down logic handles any of these if a longer window arrives
VIDEO_RUN_GRID = (124, 107, 90, 73, 56, 39, 22, 5, 1)
# what the node offers: the grid values a single clip can actually supply.
# Longer runs exist on the ladder but need a clip longer than most people
# render, and the snap-down path still handles them if one shows up.
CONTEXT_LENGTHS = [1, 5, 22, 39, 56]


def _pixel_frames(latent_t):
    """Pixel frames covered by latent_t latent steps."""
    return sum(FRAME_PER_TOKEN[k % 5] for k in range(latent_t))


def _step_offsets(latent_t):
    """Pixel-frame index at which each latent step begins."""
    out, acc = [], 0
    for k in range(latent_t):
        out.append(acc)
        acc += FRAME_PER_TOKEN[k % 5]
    return out


def _resize(image, width, height, crop):
    # image [B, H, W, C] -> [B, height, width, 3]; matches the stock helper
    samples = image[..., :3].movedim(-1, 1)
    samples = comfy.utils.common_upscale(samples, width, height, "lanczos", crop)
    return samples.movedim(1, -1)


def _encode_tail_audio(audio_vae, audio, seconds):
    """Encode the last `seconds` of a clip's audio with the H3 audio VAE.

    Returns ([1, 32, 2, T] latent, T) where T counts 40 Hz latent steps,
    matching what the layout calls ref_audio_t.
    """
    waveform = audio["waveform"]  # [B, C, L]
    sr = int(audio["sample_rate"])
    vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
    if sr != vae_sr:
        if torchaudio is None:
            raise RuntimeError(
                "h3_suite: context_audio is %d Hz but the VAE wants %d Hz "
                "and torchaudio is not available to resample." % (sr, vae_sr))
        waveform = torchaudio.functional.resample(waveform, sr, vae_sr)
    want = int(round(seconds * vae_sr))
    have = int(waveform.shape[-1])
    if have < want:
        _LOG.warning("h3_suite: context_audio is %.3fs, shorter than the "
                     "%.3fs of pinned video. Pinning what there is.",
                     have / vae_sr, seconds)
    else:
        waveform = waveform[..., have - want:]
    z = audio_vae.encode(waveform[:1].movedim(1, -1))  # [1, 32, 2, T]
    return z, int(z.shape[-1])


def _streams_from_latent(latent):
    """Unpack an H3 AV latent into its contained streams.

    NestedTensor.__getitem__ broadcasts the index into every contained
    tensor rather than selecting one, so samples[0] would strip the batch
    dimension off both streams. unbind() returns the pair.
    """
    samples = latent["samples"]
    if hasattr(samples, "unbind"):
        parts = list(samples.unbind())
    elif isinstance(samples, (tuple, list)):
        parts = list(samples)
    else:
        raise ValueError(
            "h3_suite: expected a MiniMax H3 AV latent (a nested "
            "video/audio pair), got %r" % type(samples))
    if not parts:
        raise ValueError("h3_suite: AV latent contains no streams")
    return parts


def _video_from_latent(latent):
    """Pull the video stream out of an H3 AV latent."""
    video = _streams_from_latent(latent)[0]
    if video.ndim == 4:  # unbatched [C,T,H,W]
        video = video.unsqueeze(0)
    if video.ndim != 5:
        raise ValueError("h3_suite: expected video latent [B,C,T,H,W], "
                         "got shape %s" % (tuple(video.shape),))
    return video


def _audio_tail_from_latent(latent, a_frames):
    """Slice the last `a_frames` worth of audio steps straight out of a
    generated H3 latent, skipping the decode -> re-encode round trip.

    Returns (tail latent [1, C, 2, rt], rt, overhang) where rt counts
    40 Hz latent steps and overhang is the signed fraction of a step between
    the clip's audio grid and its last pixel frame. H3 rounds to the NEAREST
    audio step: 124 frames want 206.67 and become 207 (+0.33), while 362
    frames want 603.33 and become 603 (-0.33). The caller compensates the
    placement with this signed offset so the pinned content lands exactly
    where its samples actually sit.
    """
    parts = _streams_from_latent(latent)
    if len(parts) < 2:
        raise ValueError(
            "h3_suite: context_latent has no audio stream. Wire the "
            "sampler output of an H3 AV graph, not a video-only latent.")
    video, audio = parts[0], parts[1]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if audio.ndim == 3:  # unbatched [C,2,T]
        audio = audio.unsqueeze(0)
    if audio.ndim != 4:
        raise ValueError("h3_suite: expected audio latent [B,C,2,T], "
                         "got shape %s" % (tuple(audio.shape),))
    total_t = int(audio.shape[-1])
    frames = _pixel_frames(int(video.shape[2]))
    overhang = total_t - FRAME_RESCALE * frames
    if not (-0.5 < overhang < 0.5):
        _LOG.warning(
            "h3_suite: context_latent audio grid is unexpected "
            "(%d steps for %d frames); assuming no overhang.", total_t, frames)
        overhang = 0.0
    rt = int(round(a_frames / float(FPS) * AUDIO_HZ))
    if rt > total_t:
        _LOG.warning("h3_suite: asked for %d audio steps, the latent "
                     "has %d. Pinning all of it.", rt, total_t)
        rt = total_t
    if rt < 1:
        raise ValueError("h3_suite: audio window is empty")
    tail = audio[:1, ..., total_t - rt:].clone()
    return tail, rt, float(overhang)


class H3Context:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "latent": ("LATENT",),
                # INTs, not strings: existing workflows store this widget
                # as a number, and a string list would fail ComfyUI's combo
                # validation on load. Only these values exist on the VAE
                # grid, so offering the in-betweens only ever produced a
                # snap-down warning.
                "context_length": (CONTEXT_LENGTHS, {
                    "default": 22,
                    "tooltip": "Frames of the previous clip to carry over. "
                               "Only these land on the VAE grid, so the "
                               "pinned run always ends exactly at the clip's "
                               "last frame. Bigger = smoother join, less new "
                               "content: 56 spends ~2.3s of the new clip "
                               "re-treading the old one. 1 is effectively "
                               "last-frame chaining, useful as a baseline."}),
                "encode_mode": (["video", "frames"], {
                    "default": "video",
                    "tooltip": "video: one VAE call, motion lives inside the "
                               "latent, fewer rows. frames: one call per frame, "
                               "each pinned as a separate still."}),
                "anchor_mode": (["head", "before"], {
                    "default": "head",
                    "tooltip": "head: pinned frames occupy the first indices and "
                               "come back in the output, so trim them. before: "
                               "negative indices, nothing wasted, but the "
                               "coordinates overlap the text rows."}),
                "crop": (["disabled", "center"], {"default": "disabled"}),
                "audio_context_length": ("INT", {
                    "default": 22, "min": 0, "max": 240,
                    "tooltip": "Frames of tail audio to pin, independent of the "
                               "video window. 0 follows context_length. In "
                               "timeline mode the window is END-aligned with "
                               "the pinned video, so 22 with a 22-frame video "
                               "window overlays it exactly; longer windows "
                               "extend backwards into vacated coordinate "
                               "space (untested)."}),
                "audio_mode": (["timeline", "ref"], {
                    "default": "timeline",
                    "tooltip": "timeline: pinned audio gets coordinates on "
                               "this clip's own timeline, end-aligned with "
                               "the pinned video, so the model reads it as "
                               "this clip's sound so far and continues it. "
                               "ref: stock placement in a span before the "
                               "clip, which the model imitates (similar "
                               "music, not phase-locked) rather than "
                               "continues."}),
                "seed_head": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "EXPERIMENTAL, latent path only. Also write "
                               "the pinned steps INTO the clip's starting "
                               "latent and hold them there while sampling "
                               "(temporal inpainting), so the sampler's "
                               "trajectory starts from the previous clip's "
                               "actual state instead of noise that is "
                               "merely conditioned toward it. Wire this "
                               "node's latent OUTPUT into the sampler for "
                               "this to take effect."}),
                "head_hold": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "How firmly the seeded head is held. 1.0 "
                               "keeps it exactly; lower values let the "
                               "model repaint it slightly, which can ease "
                               "the release at the boundary."}),
                "video_source": (["frames", "latent"], {
                    "default": "frames",
                    "tooltip": "frames: pin decoded frames from "
                               "context_frames (one VAE encode; each link "
                               "adds a decode/encode round trip, which "
                               "drifts color slightly down a chain). "
                               "latent: EXPERIMENTAL - slice the pinned "
                               "video straight from context_latent's tail, "
                               "no VAE round trip at all, matching what the "
                               "audio already does. Needs context_latent "
                               "wired and the same resolution as the new "
                               "clip; context_frames and encode_mode are "
                               "ignored."}),
            },
            "optional": {
                "vae": ("VAE", {
                    "tooltip": "Video VAE, used ONLY to encode context_frames "
                               "when video_source is 'frames'. The latent "
                               "path never encodes anything, so leave this "
                               "unwired there."}),
                "enabled": ("BOOLEAN", {
                    "forceInput": True,
                    "tooltip": "Wire the Project Hub's chain_active here. "
                               "False makes this node (and, via "
                               "trim_frames=0, the Trim node) pass "
                               "everything through untouched, so clip 1 of "
                               "an empty project renders with no manual "
                               "bypassing at all."}),
                "context_frames": ("IMAGE", {
                    "tooltip": "Decoded frames of the previous clip. Required "
                               "when video_source is 'frames'; ignored on the "
                               "latent path."}),
                "context_latent": ("LATENT", {
                    "tooltip": "Previous clip's SAMPLER OUTPUT latent (the same "
                               "one you wire into the decode nodes). When "
                               "supplied, the pinned audio is sliced straight "
                               "from it, skipping the decode/re-encode round "
                               "trip that dulls sound a little more at every "
                               "link of a chain. Takes priority over "
                               "context_audio; audio_vae is not needed on "
                               "this path."}),
                "audio_vae": ("VAE", {
                    "tooltip": "H3 audio VAE. Supply with context_audio to carry "
                               "the previous clip's tail sound across the join. "
                               "Not needed when context_latent is wired."}),
                "context_audio": ("AUDIO", {
                    "tooltip": "Audio of the previous clip. The tail matching the "
                               "pinned frames is encoded and pinned alongside "
                               "them. Ignored when context_latent is wired."}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "INT", "LATENT")
    RETURN_NAMES = ("conditioning", "trim_frames", "latent")
    OUTPUT_TOOLTIPS = (
        "conditioning with the pinned context applied",
        "frames of re-tread for H3 Context Trim",
        "the clip's latent, passed through - or, with seed_head on, "
        "seeded at its head with the pinned steps and carrying a noise "
        "mask that holds them during sampling. Wire THIS into the "
        "sampler when seeding; otherwise it is identical to the input.")
    FUNCTION = "apply"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Pin a run of consecutive frames from a previous clip as "
                   "never-denoised conditioning rows, so the model reads real "
                   "motion instead of guessing it from a single still.")

    def _pin_from_frames(self, vae, context_frames, context_length,
                         encode_mode, crop, width, height, frame_count):
        available = int(context_frames.shape[0])
        n = min(int(context_length), available)
        if n < 1:
            raise ValueError("h3_suite: context_frames is empty")
        if n < context_length:
            _LOG.warning("h3_suite: only %d frames supplied, pinning %d",
                         available, n)

        if encode_mode == "video":
            # snap down to the VAE grid BEFORE slicing, so the frames encoded
            # are exactly the frames the latent steps will cover (see
            # VIDEO_RUN_GRID). Slicing the last n and letting the VAE keep the
            # first `covered` of them would pin a run ending before the clip
            # does, and the join would jump by the difference.
            run = next(g for g in VIDEO_RUN_GRID if g <= n)
            if run != n:
                _LOG.warning(
                    "h3_suite: %d frames is off the VAE grid; pinning "
                    "the last %d instead (usable runs: 1, 5, 22, 39)", n, run)
            n = run

        if n >= frame_count:
            raise ValueError(
                "h3_suite: asked to pin %d frames into a %d frame clip. "
                "The pinned run must be a small fraction of the timeline."
                % (n, frame_count))

        # the LAST n frames of the incoming clip become the pinned run
        tail = _resize(context_frames[available - n:], width, height, crop)

        if encode_mode == "video":
            # one call; the VAE reads the batch axis as time and compresses
            enc = vae.encode(tail)
            if getattr(enc, "ndim", 0) != 5:
                raise ValueError(
                    "h3_suite: video-mode encode returned shape %s, "
                    "expected [B,C,T,H,W]. Try encode_mode=frames."
                    % (tuple(getattr(enc, "shape", ())),))
            steps = int(enc.shape[2])
            offsets = _step_offsets(steps)
            covered = _pixel_frames(steps)
            if covered != n:
                # n was snapped to the grid above, so a mismatch here means
                # the VAE's downscale formula changed underneath us and the
                # pinned content no longer lines up with the positions we
                # would write. Refuse rather than render a shifted join.
                raise RuntimeError(
                    "h3_suite: %d frames encoded to %d latent steps "
                    "covering %d frames; the VAE grid no longer matches "
                    "VIDEO_RUN_GRID. Upstream VAE change, refusing to run."
                    % (n, steps, covered))
            blocks = [enc[:, :, k:k + 1] for k in range(steps)]
            span = covered
        else:
            blocks, offsets = [], []
            for i in range(n):
                blocks.append(vae.encode(tail[i:i + 1]))
                offsets.append(i)
            span = n
        return blocks, offsets, span, n

    def _pin_from_latent(self, context_latent, context_length, target_video,
                         frame_count):
        """Slice the pinned run straight from the previous clip's latent.

        No decode and no re-encode: the pinned blocks ARE the previous
        clip's own latent steps, so none of the VAE round trip's color
        drift enters the chain. The catch is grid phase: latent steps
        cover (1,4,4,4,4) pixel frames cycling by ABSOLUTE step index, so
        the tail slice must start on a phase-0 boundary (step index
        divisible by 5). Then the slice's internal coverage pattern is
        identical to a fresh encode's and _step_offsets applies as-is.
        H3 clip lengths are congruent 5 mod 17 by construction, which
        makes T %% 5 == 2 and the phase-0 tail runs exactly 5, 22, 39...
        pixel frames -- the familiar grid minus the 1-frame run.

        EXPERIMENTAL: the sliced steps carry the causal VAE's temporal
        context from earlier in the previous clip, which a fresh encode of
        the same frames would not. Whether the model treats them
        identically as cond rows is an empirical question; the seam is the
        place to listen and look.
        """
        if context_latent is None:
            raise ValueError(
                "h3_suite: video_source is 'latent' but context_latent is "
                "not wired. Wire the Load Latent output, or switch "
                "video_source back to 'frames'.")
        parts = _streams_from_latent(context_latent)
        ctx = parts[0]
        if getattr(ctx, "ndim", 0) == 4:
            ctx = ctx.unsqueeze(0)
        if getattr(ctx, "ndim", 0) != 5:
            raise ValueError(
                "h3_suite: context_latent video stream has shape %s, "
                "expected [B,C,T,H,W]." % (tuple(getattr(ctx, "shape", ())),))
        if (int(ctx.shape[3]) != int(target_video.shape[3])
                or int(ctx.shape[4]) != int(target_video.shape[4])):
            raise ValueError(
                "h3_suite: context_latent is %dx%d latent but this clip is "
                "%dx%d. The latent path cannot resize; render at the same "
                "resolution or use video_source='frames'."
                % (int(ctx.shape[4]), int(ctx.shape[3]),
                   int(target_video.shape[4]), int(target_video.shape[3])))

        total = int(ctx.shape[2])
        n_req = int(context_length)
        # phase-0 tail slices: start index k0 with k0 % 5 == 0. Their pixel
        # coverage is _pixel_frames(total - k0), computable without touching
        # the tensor. Collect what this clip's length makes available.
        runs = []  # (covered, k0)
        k0 = (total // 5) * 5
        if k0 == total:
            k0 -= 5
        while k0 >= 0:
            covered = _pixel_frames(total - k0)
            runs.append((covered, k0))
            if covered > max(n_req, 39):
                break
            k0 -= 5
        usable = [(c, k) for c, k in runs if 0 < c <= n_req]
        if not usable:
            raise ValueError(
                "h3_suite: no phase-aligned tail run of <= %d frames exists "
                "in this %d-step context latent (available runs: %s frames). "
                "Raise context_length or use video_source='frames'."
                % (n_req, total, sorted(c for c, _ in runs if c > 0)))
        covered, k0 = max(usable)
        if covered != n_req:
            _LOG.warning(
                "h3_suite: %d frames is off this latent's phase grid; "
                "pinning the last %d instead (available: %s)",
                n_req, covered, sorted(c for c, _ in runs if c > 0))
        if covered >= frame_count:
            raise ValueError(
                "h3_suite: asked to pin %d frames into a %d frame clip. "
                "The pinned run must be a small fraction of the timeline."
                % (covered, frame_count))

        steps = total - k0
        blocks = [ctx[:, :, k:k + 1] for k in range(k0, total)]
        offsets = _step_offsets(steps)
        _LOG.info("h3_suite: pinned video sliced from context_latent: steps "
                  "%d..%d (%d frames), no VAE round trip", k0, total - 1,
                  covered)
        self._last_pin = (ctx, k0, steps)
        return blocks, offsets, covered

    def apply(self, conditioning, latent, context_length,
              encode_mode, anchor_mode, crop, audio_context_length=22,
              audio_mode="timeline", video_source="frames", vae=None,
              context_frames=None, context_latent=None, audio_vae=None,
              context_audio=None, enabled=True, seed_head=False,
              head_hold=1.0):
        context_length = int(context_length)
        if enabled is False:
            # inert passthrough: conditioning untouched, trim 0 makes the
            # Trim node a no-op too. The whole chain path disarms off one
            # boolean instead of a bypass ritual. No patches are activated
            # on this path - a disabled chain leaves ComfyUI stock.
            _LOG.info("h3_suite: motion context disabled (chain inactive); "
                      "passing conditioning through untouched")
            return (conditioning, 0, latent)
        _activate_inline_patches()

        video = _video_from_latent(latent)
        latent_t = int(video.shape[2])
        width = int(video.shape[4]) * 16
        height = int(video.shape[3]) * 16
        frame_count = _pixel_frames(latent_t)

        if video_source == "latent":
            blocks, offsets, n = self._pin_from_latent(
                context_latent, context_length, video, frame_count)
            span = n
        else:
            if vae is None:
                raise ValueError(
                    "h3_suite: video_source is 'frames' needs the video vae "
                    "wired to encode context_frames. Wire it, or switch "
                    "video_source to 'latent' - that path slices the pinned "
                    "video straight out of context_latent and needs no VAE.")
            if context_frames is None:
                raise ValueError(
                    "h3_suite: video_source is 'frames' but context_frames is "
                    "not wired (or its upstream is bypassed). Wire the "
                    "previous clip's decoded frames, or switch video_source "
                    "to 'latent' and wire context_latent.")
            blocks, offsets, span, n = self._pin_from_frames(
                vae, context_frames, context_length, encode_mode, crop,
                width, height, frame_count)

        if anchor_mode == "before":
            indices = [o - span for o in offsets]
        else:
            indices = list(offsets)

        keyframes = []
        for p, blk in zip(indices, blocks):
            keyframes.append({
                # stock code accepts only 0 or frame_count-1 here; the real
                # position rides under MC_KEY and the layout patch applies it
                "resolved_frame_index": 0,
                MC_KEY: p,
                "latent": blk,
            })

        values = {
            "minimax_keyframes": keyframes,
            "minimax_frame_count": frame_count,
        }

        ref_audio_t = 0
        motion_context_audio_ref = None
        a_frames = 0
        audio_src = "off"
        if context_latent is not None or context_audio is not None:
            if not payload_patch_applied():
                raise RuntimeError(
                    "h3_suite: the payload patch is not active. Without it "
                    "the audio ref would overwrite the pinned video latents and the "
                    "motion context would be lost. Check the startup log.")
            # the audio window is independent of the video one: audio cond
            # rows cost rows but never cost delivered frames
            a_frames = int(audio_context_length) or span
            if context_latent is not None:
                if context_audio is not None:
                    _LOG.info("h3_suite: both context_latent and "
                              "context_audio wired; using the latent (skips "
                              "one VAE round trip).")
                audio_latent, ref_audio_t, overhang = _audio_tail_from_latent(
                    context_latent, a_frames)
                audio_src = "latent"
            else:
                if audio_vae is None:
                    raise ValueError(
                        "h3_suite: context_audio supplied without "
                        "audio_vae. Wire the H3 audio VAE, or wire "
                        "context_latent instead.")
                audio_latent, ref_audio_t = _encode_tail_audio(
                    audio_vae, context_audio, a_frames / float(FPS))
                overhang = 0.0  # decoded audio was match_tail-cut at the frame
                audio_src = "vae"
            ref = {
                "kind": "audio",
                "ref_audio_t": ref_audio_t,
                "audio_latent": audio_latent,
            }
            if audio_mode == "timeline":
                # end-align the audio window with the pinned video: both are
                # the tail of clip A, so both must end at the same instant
                # of the new timeline -- frame `span` in head mode (where
                # A's last frame sits), frame 0 in before mode. On the
                # latent path the sliced content reaches `overhang` of a
                # step past A's last frame (H3 rounds its audio grid up),
                # so the end coordinate moves by exactly that much; the
                # layout patch takes a fractional frame index.
                end_frame = float(span if anchor_mode == "head" else 0)
                end_frame += overhang / FRAME_RESCALE
                ref[MC_AUDIO_KEY] = end_frame
            # Ref2VA multi-ref compatibility design contributed by seitanism
            # in the Banodoco MiniMax H3 seamless-extension thread.
            # Keep this separate until after the keyframe values are applied.
            # ReferenceToVideo conditioning may already contain image, video,
            # and/or audio refs; assigning minimax_refs in `values` would
            # replace all of them. Appending also guarantees that the marked
            # Motion Context audio block is last, which _fixup_audio relies on
            # to locate its slot in a multi-ref layout.
            motion_context_audio_ref = ref

        out = node_helpers.conditioning_set_values(conditioning, values)
        if motion_context_audio_ref is not None:
            out = node_helpers.conditioning_set_values(
                out, {"minimax_refs": [motion_context_audio_ref]}, append=True)

        trim = span if anchor_mode == "head" else 0
        _LOG.info("h3_suite: %s/%s, %d frames -> %d cond blocks at "
                  "indices %d..%d, %d frame clip at %dx%d, trim %d, audio %s",
                  encode_mode, anchor_mode, n, len(blocks),
                  indices[0], indices[-1], frame_count, width, height, trim,
                  ("%d frames -> %d latent steps (%.3fs) from %s, %s"
                   % (a_frames, ref_audio_t, ref_audio_t / AUDIO_HZ, audio_src,
                      "on the timeline ending at frame %.3f"
                      % float(ref.get(MC_AUDIO_KEY))
                      if audio_mode == "timeline" else "stock ref placement"))
                  if ref_audio_t else "off")
        out_latent = latent
        if seed_head and video_source == "latent":
            out_latent = self._seed_head_latent(latent, float(head_hold))
        elif seed_head:
            _LOG.warning("h3_suite: seed_head only works on the latent "
                         "path; ignoring it for video_source='frames'")
        return (out, trim, out_latent)

    def _seed_head_latent(self, latent, head_hold):
        """Write the pinned steps into the clip latent's head and attach
        a noise mask that holds them during sampling.

        ComfyUI's sampler re-noises masked regions to the current sigma
        each step (temporal inpainting), so the held head rides the
        schedule as the previous clip's content rather than being merely
        conditioned toward it. A non-nested mask applies to the VIDEO
        part; the sampler pads the audio part with ones, so audio keeps
        denoising freely - its continuity is already handled on the
        timeline."""
        import torch
        ctx, k0, steps = getattr(self, "_last_pin", (None, 0, 0))
        if ctx is None or steps <= 0:
            return latent
        samples = latent["samples"]
        parts = list(samples.unbind())
        video = parts[0].clone()
        # the pinned tail becomes the clip's opening, step for step
        video[:, :, 0:steps] = ctx[:, :, k0:k0 + steps]
        parts[0] = video
        mask = torch.ones(
            (int(video.shape[0]), 1, int(video.shape[2]),
             int(video.shape[3]), int(video.shape[4])),
            dtype=torch.float32)
        # mask semantics: 1 = denoise freely, 0 = hold to the latent
        mask[:, :, 0:steps] = 1.0 - head_hold
        out = dict(latent)
        out["samples"] = type(samples)(parts)
        out["noise_mask"] = mask
        _LOG.info("h3_suite: seeded %d head steps from the previous tail, "
                  "hold %.2f; wire this node's latent output into the "
                  "sampler", steps, head_hold)
        return out


class H3ContextTrim:
    """Drop the pinned head off a decoded clip, picture and sound together.

    The pinned frames occupy the start of the delivered timeline, so they
    have to come off before concatenating. Trimming only the images would
    leave the audio a full trim_frames longer than the video, and muxing
    those puts the whole soundtrack ahead of the picture by trim_frames/24
    seconds. At 5 frames that is 208ms, silent on ambience but squarely
    offbeat on anything with a pulse.

    So this takes both streams and removes the same span from each: whole
    frames from the images, the matching number of samples from the
    waveform. Wire trim_frames from the motion context node so the count
    follows whatever the encoder actually produced.

    The tail needs the same treatment for a different reason. H3's audio
    latent runs at 40 Hz against 24 fps picture, and FRAME_RESCALE is 5/3,
    so a 124 frame clip wants 206.67 audio steps and the layout rounds up
    to 207. Every clip therefore ships about 8.3 ms more sound than
    picture. Concatenate two and the second seam is out by 16.7 ms, three
    and it is 25 ms, and the error grows without bound down a chain. It
    reads as a faint dampening at the first join and a short click at
    later ones. Truncating the tail to exactly frames/fps stops it
    accumulating.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "trim_frames": ("INT", {"default": 0, "min": 0, "max": 4096}),
            },
            "optional": {
                "audio": ("AUDIO", {
                    "tooltip": "Decoded audio for the same clip. Trimmed by the "
                               "matching duration so sound stays locked to "
                               "picture. Leave unwired for silent clips."}),
                "fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001,
                    "tooltip": "Frame rate used to convert the trim into an "
                               "audio duration. Must match what you feed "
                               "Create Video."}),
                "match_tail": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Truncate or zero-pad audio so its duration "
                               "equals frames/fps exactly. H3 rounds its 40 Hz "
                               "audio grid to the nearest step, producing about "
                               "8ms of excess or shortage on some lengths."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "trim"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Remove the leading pinned frames from a decoded H3 clip, "
                   "trimming picture and sound by the same duration.")

    def trim(self, images, trim_frames, audio=None, fps=24.0, match_tail=True):
        n = max(0, int(trim_frames))
        total = int(images.shape[0])
        if n >= total:
            raise ValueError(
                "h3_suite: asked to trim %d frames from a %d frame clip"
                % (n, total))
        out_images = images[n:] if n else images

        out_audio = audio
        if audio is not None:
            waveform = audio["waveform"]
            sr = int(audio["sample_rate"])
            seconds = n / float(fps)
            cut = int(round(seconds * sr))
            length = int(waveform.shape[-1])
            if cut >= length:
                raise ValueError(
                    "h3_suite: trimming %.3fs from %.3fs of audio would "
                    "leave nothing. Check that fps matches the clip."
                    % (seconds, length / sr))
            waveform = waveform[..., cut:]

            if match_tail:
                frames_left = total - n
                want = int(round(frames_left / float(fps) * sr))
                have = int(waveform.shape[-1])
                if have > want:
                    over = have - want
                    waveform = waveform[..., :want]
                    _LOG.info("h3_suite: tail trimmed %d samples "
                              "(%.2fms) so audio matches %d frames exactly",
                              over, over / sr * 1000.0, frames_left)
                elif have < want:
                    missing = want - have
                    waveform = torch.nn.functional.pad(waveform, (0, missing))
                    _LOG.info("h3_suite: tail padded %d zero samples "
                              "(%.2fms) so audio matches %d frames exactly",
                              missing, missing / sr * 1000.0, frames_left)

            out_audio = {"waveform": waveform, "sample_rate": sr}
            _LOG.info("h3_suite: %d frames / %.4fs picture, %.4fs sound, "
                      "drift %.2fms",
                      total - n, (total - n) / float(fps),
                      int(waveform.shape[-1]) / sr,
                      abs((total - n) / float(fps) - int(waveform.shape[-1]) / sr) * 1000.0)
        elif n:
            _LOG.info("h3_suite: trimmed %d leading frames, %d remain. "
                      "No audio wired; if this clip has sound, mux it through "
                      "this node or it will run %.3fs ahead of the picture.",
                      n, total - n, n / float(fps))

        return (out_images, out_audio)


def _resolve_latent_path(path, clip_index=0):
    """Turn the loader's path input into a concrete file.

    Accepts an absolute path, a path relative to ComfyUI's output folder,
    or a directory (in either form). For a directory:

      clip_index == 0   the NEWEST .safetensors inside is used. Simple,
                        but NOT retry-safe: re-rolling a clip loads the
                        rejected attempt's own save (see the node docs).
                        Its run counter also numbers ATTEMPTS, not clips.
      clip_index  > 0   exactly that clip's slot is loaded: clip 1 is
                        *_00001.safetensors. Auto-mode files carry a
                        trailing underscore (*_00001_.safetensors) and
                        are never matched, because their numbers count
                        runs and could hold a reject.
    """
    p = (path or "").strip().strip('"').strip("'")
    if not p:
        p = "h3_context"
    candidates = [p, os.path.join(folder_paths.get_output_directory(), p)]
    for c in candidates:
        if os.path.isfile(c):
            return c
        if os.path.isdir(c):
            idx = int(clip_index)
            if idx > 0:
                # indexed slots use the natural name: clip 2 lives in
                # *_00002.safetensors. Auto-mode files carry a trailing
                # underscore (*_00002_.safetensors) and are deliberately
                # NOT matched: their numbers count runs, not clips, so a
                # reject could be sitting in any of them.
                endings = ("_%05d.safetensors" % idx,
                           "_clip%03d.safetensors" % idx)  # older versions
                files = [os.path.join(c, f) for f in os.listdir(c)
                         if f.endswith(endings)]
                if not files:
                    near = [f for f in os.listdir(c)
                            if f.endswith("_%05d_.safetensors" % idx)]
                    hint = ""
                    if near:
                        hint = (" Found %s, which is an auto-numbered save "
                                "(trailing underscore = numbered by RUN, so "
                                "it may be a reject). If it really is clip "
                                "%d, rename it to drop the trailing "
                                "underscore: %s" %
                                (near[0], idx,
                                 near[0].replace("_%05d_" % idx,
                                                 "_%05d" % idx)))
                    raise FileNotFoundError(
                        "h3_suite: no saved latent for clip %d "
                        "(no *_%05d.safetensors in %s).%s"
                        % (idx, idx, c, hint))
            else:
                files = [os.path.join(c, f) for f in os.listdir(c)
                         if f.endswith(".safetensors")]
                if not files:
                    raise FileNotFoundError(
                        "h3_suite: no saved latents in %s. Run a "
                        "clip with the Save Latent node first." % c)
            return max(files, key=os.path.getmtime)

    # The Save node's field is a filename PREFIX ("folder/name" writes
    # name_00002.safetensors), while this one documents a FOLDER. Typing the
    # same string into both is the obvious thing to do and used to fail with
    # "neither a file nor a folder" AFTER a successful save -- the worst
    # possible moment. So accept a prefix here too: treat the last component
    # as a filename stem and match inside its parent.
    for c in candidates:
        parent, stem = os.path.split(c)
        if not stem or not os.path.isdir(parent):
            continue
        idx = int(clip_index)
        if idx > 0:
            endings = ("_%05d.safetensors" % idx,
                       "_clip%03d.safetensors" % idx)
        else:
            endings = (".safetensors",)
        files = [os.path.join(parent, f) for f in os.listdir(parent)
                 if f.startswith(stem) and f.endswith(endings)
                 and not f.endswith("_%05d_.safetensors" % idx)]
        if files:
            return max(files, key=os.path.getmtime)
    raise FileNotFoundError(
        "h3_suite: %r is neither a file nor a folder (also tried "
        "relative to the ComfyUI output directory)." % p)


class H3ContextSaveLatent:
    """Save an H3 AV latent to disk so the NEXT run can load it.

    Wiring the sampler's output straight into context_latent is a cycle:
    the sampler would be consuming its own result. The latent that motion
    context needs is the PREVIOUS clip's, which lives in the previous run
    -- so it has to cross runs through disk, the same way the frames and
    audio already do. Stock Save/Load Latent can't serialise H3's nested
    video/audio pair; this saves the two streams side by side.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "The sampler's output latent (the same one "
                               "you wire into the decode nodes)."}),
                "filename_prefix": ("STRING", {
                    "default": "h3_context/clip",
                    "tooltip": "Saved under the ComfyUI output folder. The "
                               "default keeps all chain latents in one "
                               "folder so the Load node can always pick "
                               "the newest."}),
                "clip_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999,
                    "tooltip": "Which clip of the chain THIS is. Saves to "
                               "that clip's fixed slot, so a re-roll "
                               "overwrites its own reject instead of "
                               "stacking new files. Generating clip 2: "
                               "set 2 here and 1 on the Load node. 0 = "
                               "old behaviour, a new numbered file every "
                               "run (numbers count runs, not clips)."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("latent_path",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Save the sampler's AV latent so the next run's Motion "
                   "Context node can pin audio from it via the matching "
                   "Load node.")

    def save(self, latent, filename_prefix, clip_index=0):
        if _st_save is None:
            raise RuntimeError("h3_suite: safetensors is not "
                               "available; cannot save latents.")
        parts = _streams_from_latent(latent)
        if len(parts) < 2:
            raise ValueError(
                "h3_suite: latent has no audio stream; wire the "
                "sampler output of an H3 AV graph.")
        video = parts[0].cpu().contiguous()
        audio = parts[1].cpu().contiguous()
        folder, filename, counter, _, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory())
        if int(clip_index) > 0:
            # fixed slot with the natural name: clip 2 -> *_00002. A
            # re-roll of this clip overwrites its own save, so rejects
            # never accumulate or get loaded later. Auto mode (below)
            # keeps a trailing underscore, which is what excludes its
            # run-numbered files from indexed loading.
            path = os.path.join(folder, "%s_%05d.safetensors"
                                % (filename, int(clip_index)))
        else:
            path = os.path.join(folder, "%s_%05d_.safetensors"
                                % (filename, counter))
        _st_save({"video": video, "audio": audio}, path,
                 metadata={"format": "h3_motion_context_av_v1"})
        _LOG.info("h3_suite: saved AV latent to %s (video %s, "
                  "audio %s)", path, tuple(video.shape), tuple(audio.shape))
        return (path,)


class H3ContextLoadLatent:
    """Load a saved H3 AV latent for the context_latent input.

    clip_index means exactly what it says: set it to the clip you want to
    CONTINUE FROM, and that clip's slot is loaded. Generating clip 2 from
    clip 1: Load node 1, Save node 2. Re-rolling clip 2 changes nothing --
    it reloads slot 1 and overwrites slot 2's reject. Accept, then bump
    both numbers.

    At 0 it loads the newest file in the folder instead. Simple, but NOT
    retry-safe: a re-roll's newest file is the rejected attempt's own
    save, so the retry gets conditioned on the audio you just rejected.

    The output is ONLY for the Motion Context node's context_latent input.
    It is not a decodable latent -- do not wire it into VAE decode.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent_path": ("STRING", {
                    "default": "h3_context",
                    "tooltip": "A file, a folder, or the same filename "
                               "prefix you gave the Save node (relative "
                               "paths resolve against the ComfyUI output "
                               "directory). Pointing at a specific FILE "
                               "always loads that file, ignoring "
                               "clip_index."}),
                "clip_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999,
                    "tooltip": "The clip to CONTINUE FROM: that clip's "
                               "slot is loaded. Generating clip 2 from "
                               "clip 1: set 1 here and 2 on the Save "
                               "node. 0 = newest file in the folder "
                               "(NOT retry-safe: a re-roll loads its own "
                               "rejected audio)."}),
            },
        }

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "load"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Load a latent saved by H3 Context Save Latent, "
                   "for the context_latent input only.")

    @classmethod
    def IS_CHANGED(cls, latent_path, clip_index=0):
        # the path string stays constant while the file behind it changes
        # (newest save, or an overwritten slot), so cache on the resolved
        # file identity instead -- otherwise ComfyUI would happily serve
        # a stale latent forever
        try:
            p = _resolve_latent_path(latent_path, clip_index)
            return "%s:%d" % (p, os.stat(p).st_mtime_ns)
        except Exception:
            return float("NaN")  # unresolvable: never cache

    def load(self, latent_path, clip_index=0):
        if _st_load is None:
            raise RuntimeError("h3_suite: safetensors is not "
                               "available; cannot load latents.")
        path = _resolve_latent_path(latent_path, clip_index)
        data = _st_load(path)
        if "video" not in data or "audio" not in data:
            raise ValueError(
                "h3_suite: %s is not an H3 AV context latent "
                "(missing video/audio streams). Was it saved by the stock "
                "Save Latent node instead?" % path)
        _LOG.info("h3_suite: loaded AV latent from %s", path)
        # a plain list, not a NestedTensor: only this repo's context_latent
        # input accepts it, which is the point -- it cannot be mistaken
        # for a decodable latent without failing loudly downstream
        return ({"samples": [data["video"], data["audio"]]},)


NODE_CLASS_MAPPINGS = {
    "H3Context": H3Context,
    "H3ContextTrim": H3ContextTrim,
    "H3ContextSaveLatent": H3ContextSaveLatent,
    "H3ContextLoadLatent": H3ContextLoadLatent,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3Context": "H3 Context",
    "H3ContextTrim": "H3 Context Trim",
    "H3ContextSaveLatent": "H3 Context Save Latent",
    "H3ContextLoadLatent": "H3 Context Load Latent",
}
