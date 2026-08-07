"""Let keyframes and refs coexist.

`MiniMaxH3.extra_conds` in comfy/model_base.py fills the payload from two
independent `if` blocks. The keyframe block sets `cond_video_latents`, then
the refs block **overwrites** it:

    if keyframes is not None:
        payload["cond_video_latents"] = [kf["latent"] for kf in keyframes]
    if refs is not None:
        payload["cond_video_latents"] = [r["latent"] for r in refs if "latent" in r]
        payload["cond_audio_latents"] = [r["audio_latent"] for r in refs ...]

So attaching an audio-only ref alongside keyframes wipes the keyframe video
content: an audio-only block has no "latent" key, the list comes back empty,
and the cond rows the layout built have nothing to fill them.

The layout itself handles the combination fine. Keyframe cond rows are
emitted first, ref rows second, target rows last, which is exactly the order
the forward pass expects when it writes rows into the never-denoised slots.
Only this payload assignment is in the way.

This wrapper re-runs the same logic and concatenates instead, keeping
keyframe latents first to match the row order. Graphs using only one
mechanism are unaffected: with no refs the ref list is empty, with no
keyframes the keyframe list is.
"""

import logging

import comfy.model_base as model_base

_LOG = logging.getLogger("h3_motion_context")

_orig_extra_conds = None
_applied = False


def _patched_extra_conds(self, **kwargs):
    out = _orig_extra_conds(self, **kwargs)

    keyframes = kwargs.get("minimax_keyframes", None)
    refs = kwargs.get("minimax_refs", None)
    if not keyframes or not refs:
        return out  # only one mechanism in play, stock behaviour is correct

    cond = out.get("minimax_payload", None)
    payload = getattr(cond, "cond", None) if cond is not None else None
    if not isinstance(payload, dict):
        _LOG.warning("h3_motion_context: could not reach the H3 payload, "
                     "keyframe latents may have been overwritten by refs")
        return out

    kf_video = [kf["latent"] for kf in keyframes if "latent" in kf]
    ref_video = [r["latent"] for r in refs if "latent" in r]
    payload["cond_video_latents"] = kf_video + ref_video
    payload["cond_audio_latents"] = [r["audio_latent"] for r in refs
                                     if r.get("audio_latent") is not None]
    # only write frame_count when we actually have one. This wrapper fires
    # for ANY graph combining keyframes and refs, not just ours; a graph
    # that reaches here without minimax_frame_count may have a valid value
    # already set by the original, and overwriting it with None would break
    # the last-frame anchor branch downstream.
    fc = kwargs.get("minimax_frame_count", None)
    if fc is not None:
        payload["frame_count"] = fc
    return out


def apply_patch():
    global _orig_extra_conds, _applied
    if _applied:
        return True
    cls = getattr(model_base, "MiniMaxH3", None)
    if cls is None or not hasattr(cls, "extra_conds"):
        _LOG.warning("h3_motion_context: MiniMaxH3.extra_conds not found, "
                     "keyframes and refs cannot be combined")
        return False
    _orig_extra_conds = cls.extra_conds
    cls.extra_conds = _patched_extra_conds
    _applied = True
    _LOG.info("h3_motion_context: keyframe/ref coexistence enabled")
    return True


def is_applied():
    return _applied
