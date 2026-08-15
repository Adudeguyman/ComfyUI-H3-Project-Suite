"""Lazy AV denoise-mask payload compatibility for H3 masked-video features.

Normal Motion Context/MultiRef never installs this wrapper. It is reached only
through ``ensure_existing_video_compat()`` when a masked extension/bridge node
runs.

Native ComfyUI support always wins. Detection intentionally avoids depending on
``inspect.getsource()`` so packaged/compiled builds and harmless upstream
refactors still self-retire the compatibility wrapper when PR #15375-equivalent
MiniMaxH3 payload support is present.
"""

# Vendored from seitanism/ComfyUI-H3-Motion-Context-MultiRef (GPL-3.0),
# NikoDemon80 Motion Context lineage. The mechanism is ComfyUI PR #15375
# by drozbay (AbleJones on the Banodoco Discord). Marker strings are kept
# IDENTICAL to the source pack so both packs coexist without double-
# installing: whichever runs first installs, the other recognises it.
#
# Nothing here touches files on disk. Capabilities are checked against the
# live ComfyUI first and native support always wins; compatibility is
# installed only for missing pieces, in memory, and a restart reverts it.


from __future__ import annotations

import functools
import inspect
import logging
import types


_LOG = logging.getLogger(__name__)
_MARKER = "_h3_existing_video_av_mask_payload_compat_v2"


def _walk_wrapped(fn):
    seen = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        yield fn
        fn = getattr(fn, "__wrapped__", None)


def _is_ours(fn):
    code = getattr(fn, "__code__", None)
    return bool(
        getattr(fn, _MARKER, False)
        and code is not None
        and code.co_name == "wrapper"
        and code.co_filename == __file__
    )


def _signature_has(fn, *names):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(name in params for name in names)


def _code_strings(code):
    """Yield strings/names from a function and any nested code objects."""
    if not isinstance(code, types.CodeType):
        return
    yield from code.co_names
    def walk_const(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, types.CodeType):
            yield from _code_strings(value)
        elif isinstance(value, (tuple, list, set, frozenset)):
            for item in value:
                yield from walk_const(item)

    for value in code.co_consts:
        yield from walk_const(value)


def _function_mentions_native_payload(fn):
    """Structural probe for native video+audio mask conditions.

    Bytecode constants are available even when source files are stripped, and
    unlike the old source-text check this survives formatting/helper movement
    inside ``extra_conds``.
    """
    for item in _walk_wrapped(fn):
        if _is_ours(item):
            continue
        strings = set(_code_strings(getattr(item, "__code__", None)) or ())
        if "denoise_mask" in strings and "audio_denoise_mask" in strings:
            return True
    return False


def _native_h3_mask_hooks(cls):
    """Recognize the native model_base half of PR #15375 (or equivalent).

    The compatibility layer installs process_denoise_mask/scale_latent_inpaint,
    but deliberately does *not* install MiniMaxH3.process_timestep. Requiring a
    subclass-owned process_timestep that accepts both AV mask names therefore
    distinguishes native core integration from our fallback after it has been
    activated.
    """
    if cls is None:
        return False
    process_step = cls.__dict__.get("process_timestep")
    process_mask = cls.__dict__.get("process_denoise_mask")
    scale = cls.__dict__.get("scale_latent_inpaint")
    return bool(
        callable(process_step)
        and _signature_has(process_step, "denoise_mask", "audio_denoise_mask")
        and callable(process_mask)
        and callable(scale)
    )


def _native_av_mask_payload(cls, fn):
    # Direct evidence takes priority. The native-hook fingerprint is the
    # self-retirement path for upstream refactors that move payload extraction
    # into a helper and leave no literal output-key strings in extra_conds.
    return bool(
        fn
        and (
            _function_mentions_native_payload(fn)
            or _native_h3_mask_hooks(cls)
        )
    )


def capability_status():
    import comfy.model_base as model_base
    cls = getattr(model_base, "MiniMaxH3", None)
    fn = getattr(cls, "extra_conds", None) if cls is not None else None
    direct = bool(fn and _function_mentions_native_payload(fn))
    native_hooks = _native_h3_mask_hooks(cls)
    native = bool(fn and (direct or native_hooks))
    return {
        "available": fn is not None,
        "native_av_mask_payload": native,
        "native_payload_direct": direct,
        "native_h3_mask_hooks": native_hooks,
        "wrapper_present": bool(fn and any(_is_ours(x) for x in _walk_wrapped(fn))),
    }


def _add_av_mask_conditions(out, kwargs):
    if not isinstance(out, dict):
        return
    have_video = "denoise_mask" in out
    have_audio = "audio_denoise_mask" in out
    if have_video and have_audio:
        return

    denoise_mask = kwargs.get("denoise_mask")
    latent_shapes = kwargs.get("latent_shapes")
    if denoise_mask is None or latent_shapes is None or len(latent_shapes) < 2:
        return
    import torch
    import comfy.conds
    import comfy.utils as utils

    masks = utils.unpack_latents(denoise_mask, latent_shapes)
    if len(masks) < 2:
        return
    if not have_video and torch.amin(masks[0]).item() < 1.0 - 1e-3:
        out["denoise_mask"] = comfy.conds.CONDRegular(masks[0][:, :1].clone())
    if not have_audio and torch.amin(masks[1]).item() < 1.0 - 1e-3:
        out["audio_denoise_mask"] = comfy.conds.CONDRegular(masks[1][:, :1].clone())


def _make_wrapper(base):
    @functools.wraps(base, updated=())
    def wrapper(self, **kwargs):
        out = base(self, **kwargs)
        _add_av_mask_conditions(out, kwargs)
        return out
    setattr(wrapper, _MARKER, True)
    return wrapper


def ensure_av_mask_payload_compat():
    import comfy.model_base as model_base
    cls = getattr(model_base, "MiniMaxH3", None)
    if cls is None or not hasattr(cls, "extra_conds"):
        raise RuntimeError("h3_suite: MiniMaxH3.extra_conds not found")
    current = cls.extra_conds
    if _native_av_mask_payload(cls, current):
        return True
    if _is_ours(current):
        return True
    cls.extra_conds = _make_wrapper(current)
    _LOG.info("h3_suite: PR #15375 AV-mask payload compatibility enabled")
    return True
