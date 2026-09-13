"""Capability-aware runtime compatibility for ComfyUI PR #15375.

This module contains only the model-level H3 AV-mask behavior required by the
existing-video extension. It deliberately does NOT touch ``MiniMaxH3.extra_conds``;
AV-mask payload extraction lives in :mod:`h3_mask_payload_compat`. Classic Motion
Context/Ref2VA uses native #15439 core and never installs this layer.

Every capability is checked against the live ComfyUI implementation. Native
support wins; compatibility is installed only for missing pieces. Restarting
ComfyUI still reverts all runtime modifications.

The compatibility snapshot tracks the PR's fractional/8-bit mask behavior as
reviewed on 2026-08-11, but intentionally targets the aligned AV masks produced
by this repo's existing-video node rather than acting as a general sampler patch.
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
#
# The replacement functions are ordinary definitions below. They reach
# everything they need through the target module object (``h3m.torch``,
# ``h3m.PackedLayout`` and so on) at call time, so they behave exactly as
# if they had been defined inside that module, without building code
# from strings.


from __future__ import annotations

import inspect
import logging

_LOG = logging.getLogger(__name__)
_MARKER = "_h3_motion_context_pr15375_compat_v2"


def _mark(fn):
    try:
        setattr(fn, _MARKER, True)
    except Exception:
        pass
    return fn


def _is_ours(fn):
    return bool(getattr(fn, _MARKER, False))


def _signature_has(fn, *names):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(name in params for name in names)


def capability_status():
    import comfy.model_base as model_base
    import comfy.ldm.minimax.model as h3m

    cls = getattr(model_base, "MiniMaxH3", None)
    process = getattr(cls, "process_denoise_mask", None) if cls else None
    scale = getattr(cls, "scale_latent_inpaint", None) if cls else None

    process_native = bool(
        cls
        and "process_denoise_mask" in cls.__dict__
        and callable(process)
        and not _is_ours(process)
    )
    scale_native = bool(
        cls
        and "scale_latent_inpaint" in cls.__dict__
        and callable(scale)
        and not _is_ours(scale)
    )

    forward = getattr(getattr(h3m, "MiniMaxH3Model", None), "forward", None)
    inner = getattr(getattr(h3m, "MiniMaxH3Model", None), "_forward", None)
    final = getattr(getattr(h3m, "FinalLayer", None), "forward", None)
    engine_indicators = {
        "mask_row_values": callable(getattr(h3m, "mask_row_values", None)),
        "mod_row": callable(getattr(h3m, "_mod_row", None)),
        "forward_masks": callable(forward) and _signature_has(
            forward, "denoise_mask", "audio_denoise_mask"
        ),
        "inner_masks": callable(inner) and _signature_has(
            inner, "denoise_mask", "audio_denoise_mask"
        ),
        "final_layer": callable(final),
    }
    engine_complete = all(engine_indicators.values())
    engine_ours = bool(
        callable(forward)
        and callable(inner)
        and _is_ours(forward)
        and _is_ours(inner)
    )

    return {
        "process_denoise_mask_native": process_native,
        "process_denoise_mask_compat": bool(callable(process) and _is_ours(process)),
        "scale_latent_inpaint_native": scale_native,
        "scale_latent_inpaint_compat": bool(callable(scale) and _is_ours(scale)),
        "mask_engine_complete": engine_complete,
        "mask_engine_native": bool(engine_complete and not engine_ours),
        "mask_engine_compat": engine_ours,
        "mask_engine_indicators": engine_indicators,
    }


def _engine_functions(h3m):
    """The PR #15375 diffusion-model helpers and methods, bound to ``h3m``.

    Every module-level name the originals used (torch, comfy, PackedLayout,
    the pack/unpack helpers, the cond timesteps) is read off ``h3m`` when
    the function runs, which is the same lookup the module's own functions
    perform. The helpers this layer installs (mask_row_values, _mod_row)
    are reached the same way, so the group stays consistent with whatever
    is actually installed on the module.
    """

    def mask_row_values(mask, latent_t, lat_h, lat_w):
        # [T, H, W] denoise mask (1 = generate) -> per-2x2-patch-row float in [0, 1],
        # None when every row fully generates
        torch = h3m.torch
        m = torch.nn.functional.pad(
            mask, (0, lat_w - mask.shape[-1], 0, lat_h - mask.shape[-2]),
            mode="replicate")
        m = m.reshape(latent_t, lat_h // 2, 2, lat_w // 2, 2).amax(dim=(2, 4))
        values = m.reshape(-1)
        if bool((values >= 1.0 - 1e-3).all()):
            return None
        return values

    def _mod_row(vecs, row, dtype):
        # row is a mod-row index, or a per-token LongTensor of mod-row indices
        return vecs[row].to(dtype)

    def _mod_scale_shift(h, shift, scale, segments):
        # segments: [(start, stop, mod_row)] covering h contiguously.
        for a, b, row in segments:
            h[a:b].mul_(1.0 + h3m._mod_row(scale, row, h.dtype)).add_(
                h3m._mod_row(shift, row, h.dtype))
        return h

    def _mod_gate(x, gate, other, segments):
        # other is the fresh attn/mlp output: accumulate the gated residual
        # into the stream in place, one fused kernel per segment
        for a, b, row in segments:
            x[a:b].addcmul_(other[a:b], h3m._mod_row(gate, row, x.dtype))
        return x

    def final_forward(self, x, t_emb, video_seg, audio_seg):
        # video_seg / audio_seg: (start, stop, row) of the target streams, where row
        # is a mod-row index or a per-token blend (see _mod_row)
        torch = h3m.torch
        shift, scale = self.adaln_proj(t_emb)

        def mod(seg):
            a, b, row = seg
            return (self.norm(x[a:b]) * (1.0 + h3m._mod_row(scale, row, scale.dtype))
                    + h3m._mod_row(shift, row, shift.dtype)).to(torch.float32)

        return self.video_out(mod(video_seg)), self.audio_out(mod(audio_seg))

    def forward(self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
        # the sampler carries the audio as (sigma_v / sigma_a) * x_audio; undo it outside
        # the wrappers so they and the network see the stream's own latent and velocity
        comfy = h3m.comfy
        scale = float((minimax_payload or {}).get("audio_scale", 1.0))
        audio_src = x[1]
        if scale != 1.0:
            shift_v = float(transformer_options.get(
                "minimax_h3_sigma_shift_video", self.sigma_shift_video))
            shift_a = float(transformer_options.get(
                "minimax_h3_sigma_shift_audio", self.sigma_shift_audio))
            sigma_v = (timestep.flatten()[0] / 1000.0).float().clamp(min=1e-6)
            sigma_a = h3m.time_shift_sigma(sigma_v, shift_v, shift_a)
            carry = (sigma_a / sigma_v).to(audio_src.dtype)
            x = [x[0], audio_src * carry]

        out = comfy.patcher_extension.WrapperExecutor.new_class_executor(
            self._forward,
            self,
            comfy.patcher_extension.get_all_wrappers(
                comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL,
                transformer_options)
        ).execute(x, timestep, context, transformer_options,
                  minimax_payload=minimax_payload,
                  denoise_mask=denoise_mask,
                  audio_denoise_mask=audio_denoise_mask, **kwargs)

        if scale != 1.0:
            # d/d(sigma_v) of the carried variable
            out[1] = ((1.0 - scale) * (audio_src * carry)
                      + (1.0 + (scale - 1.0) * sigma_a).to(out[1].dtype) * out[1])
        return out

    def _forward(self, x, timestep, context, transformer_options={},
                 minimax_payload=None, denoise_mask=None,
                 audio_denoise_mask=None, **kwargs):
        torch = h3m.torch
        comfy = h3m.comfy
        video_x, audio_x = x[0], x[1]
        orig_t, orig_h, orig_w = video_x.shape[2], video_x.shape[3], video_x.shape[4]
        video_x = comfy.ldm.common_dit.pad_to_patch_size(video_x, self.patch_size)
        if video_x.shape[0] != 1:
            raise ValueError("MiniMax H3 supports batch size 1")
        payload = minimax_payload or {}
        device = video_x.device
        dtype = context.dtype  # compute dtype

        latent_t, lat_h, lat_w = video_x.shape[2], video_x.shape[3], video_x.shape[4]
        audio_t = audio_x.shape[-1]
        text_len = context.shape[1]
        # extra_conds prebuilds the layout once per sampling run
        layout = payload.get("layout")
        if layout is None or layout.signature != (text_len, latent_t, lat_h, lat_w, audio_t):
            layout = h3m.PackedLayout(text_len, latent_t, lat_h, lat_w, audio_t,
                                      keyframes=payload.get("keyframes"),
                                      refs=payload.get("refs"))

        # model_base passes model_sampling.timestep(sigma) = sigma * 1000
        shift_v = float(transformer_options.get(
            "minimax_h3_sigma_shift_video", self.sigma_shift_video))
        shift_a = float(transformer_options.get(
            "minimax_h3_sigma_shift_audio", self.sigma_shift_audio))
        sigma_v = (timestep.flatten()[0] / 1000.0).float().clamp(min=1e-6)
        t_v = float(1.0 - sigma_v)
        t_a = float(1.0 - h3m.time_shift_sigma(sigma_v, shift_v, shift_a))

        # distinct timesteps are known analytically: text/pad follow video, cond rows pin near 1
        vis_aug = float(payload.get("visual_cond_noise_aug", h3m.VISUAL_COND_TIMESTEP))
        aud_aug = float(payload.get("audio_cond_noise_aug", h3m.AUDIO_COND_TIMESTEP))
        seg_t = {"text": t_v, "video": t_v, "audio": t_a,
                 "cond": max(t_v, vis_aug), "ref_img": max(t_v, vis_aug),
                 "cond_audio": max(t_a, aud_aug), "ref_audio": max(t_a, aud_aug)}

        # masked rows run at their own strength: mask value m puts a row at sigma = m * sigma_stream,
        # so its label is 1 - m * sigma, clamped at the cond timestep for fully preserved rows
        t_pin_v = max(t_v, h3m.VISUAL_COND_TIMESTEP)
        t_pin_a = max(t_a, h3m.AUDIO_COND_TIMESTEP)
        video_rows_t = None
        audio_rows_t = None
        if denoise_mask is not None:
            m = h3m.mask_row_values(denoise_mask[0, 0].to(torch.float32),
                                    latent_t, lat_h, lat_w)
            if m is not None:
                rows_t = (1.0 - m * sigma_v.to(m.device)).clamp(max=t_pin_v)
                if rows_t.unique().numel() == 1:
                    seg_t["video"] = float(rows_t[0])
                else:
                    video_rows_t = rows_t
        if audio_denoise_mask is not None:
            m = audio_denoise_mask[0, 0].to(torch.float32).reshape(-1)
            if not bool((m >= 1.0 - 1e-3).all()):
                sigma_a = 1.0 - t_a
                rows_t = (1.0 - m * sigma_a).clamp(max=t_pin_a)
                if rows_t.unique().numel() == 1:
                    seg_t["audio"] = float(rows_t[0])
                else:
                    audio_rows_t = rows_t

        unique_t = sorted({t_v, t_a} | {seg_t[k] for _, _, k in layout.segments}
                          | (set(video_rows_t.unique().tolist()) if video_rows_t is not None else set())
                          | (set(audio_rows_t.unique().tolist()) if audio_rows_t is not None else set()))
        t_row = {t: i for i, t in enumerate(unique_t)}
        seg_tag = {"text": 1, "video": 0, "audio": 2, "cond": 0, "ref_img": 0,
                   "cond_audio": 2, "ref_audio": 2}

        def rows_to_mod_index(rows_t, tag):
            # per-row timestep values -> per-row mod-row indices into the t_emb table
            levels = rows_t.unique()
            base = torch.tensor([t_row[v] * 3 + tag for v in levels.tolist()],
                                dtype=torch.long, device=rows_t.device)
            return base[torch.searchsorted(levels, rows_t)]

        text_tags = payload.get("text_token_tags")
        mod_segments = []
        for a, b, kind in layout.segments:
            row_base = t_row[seg_t[kind]] * 3
            if kind == "text" and text_tags is not None:
                # the presentation text span mixes tags (vision pads carry the video modality) split into tag runs
                tags = text_tags.view(-1).tolist()
                run_start = 0
                for i in range(1, b - a + 1):
                    if i == b - a or tags[i] != tags[run_start]:
                        mod_segments.append((a + run_start, a + i, row_base + int(tags[run_start])))
                        run_start = i
            elif kind == "video" and video_rows_t is not None:
                mod_segments.append((a, b, rows_to_mod_index(video_rows_t, seg_tag[kind])))
            elif kind == "audio" and audio_rows_t is not None:
                mod_segments.append((a, b, rows_to_mod_index(audio_rows_t, seg_tag[kind])))
            else:
                mod_segments.append((a, b, row_base + seg_tag[kind]))

        # embed
        img_update = layout.img_update.to(device)
        audio_update = layout.audio_update.to(device)
        video_rows = h3m.patchify_video(video_x.to(torch.float32), self.patch_size)
        audio_rows = h3m.pack_audio(audio_x.to(torch.float32))
        cond_video_rows = self._cond_video_rows(payload, device)
        cond_audio_rows = self._cond_audio_rows(payload, device)

        all_video_rows = video_rows
        if cond_video_rows is not None:
            all_video_rows = torch.empty(img_update.shape[0], video_rows.shape[1],
                                         dtype=torch.float32, device=device)
            all_video_rows[~img_update] = cond_video_rows
            all_video_rows[img_update] = video_rows
        all_audio_rows = audio_rows
        if cond_audio_rows is not None:
            all_audio_rows = torch.empty(audio_update.shape[0], audio_rows.shape[1],
                                         dtype=torch.float32, device=device)
            all_audio_rows[~audio_update] = cond_audio_rows
            all_audio_rows[audio_update] = audio_rows

        video_embed = self.video_patch_proj(all_video_rows).to(dtype)
        audio_embed = self.audio_patch_proj(all_audio_rows).to(dtype)
        text_states = context[0]
        if text_states.shape[-1] != self.hidden_size:
            text_states = self.token_refiner(self.condition_proj(text_states),
                                             transformer_options=transformer_options)

        # segments are contiguous: assemble by slices, embed rows follow segment order
        h = torch.empty(layout.seq_len, self.hidden_size, dtype=dtype, device=device)
        voff = aoff = 0
        for a, b, kind in layout.segments:
            n = b - a
            if kind == "text":
                h[a:b] = text_states
            elif kind in ("cond", "ref_img", "video"):
                h[a:b] = video_embed[voff:voff + n]
                voff += n
            else:  # ref_audio / audio
                h[a:b] = audio_embed[aoff:aoff + n]
                aoff += n

        t_vals = torch.tensor(unique_t, dtype=torch.float32, device=device)
        if self.use_adaln_curves:
            # adaln projections consume interpolated coordinates of the time-embedding curve
            table = comfy.model_management.cast_to(self.adaln_t_table, device=device)
            pos = t_vals.clamp(0.0, 1.0) * (table.shape[0] - 1)     # t in [0,1] -> fractional grid index, out-of-range t clamps to the curve ends
            i0 = pos.floor().long().clamp(max=table.shape[0] - 2)   # lower grid row, max-clamp keeps t=1.0 on the last interval instead of reading past the table
            t_emb = torch.lerp(table[i0], table[i0 + 1], (pos - i0).unsqueeze(1))  # blend the two rows by the fractional part
        else:
            t_emb = self.time_embedder(t_vals).to(dtype)

        # rotation table computed once per forward, consumed by the kitchen split-half rope
        rope_freqs = h3m.rope_rotation_table(
            self.rope_freqs(layout.position_ids, device), dtype)

        # blocks
        patches_replace = transformer_options.get("patches_replace", {})
        blocks_replace = patches_replace.get("dit", {})
        prefetch_queue = comfy.model_prefetch.make_prefetch_queue(
            list(self.blocks), device, transformer_options)
        for i, block in enumerate(self.blocks):
            comfy.model_prefetch.prefetch_queue_pop(prefetch_queue, device, block)
            if ("double_block", i) in blocks_replace:
                def block_wrap(args):
                    return {"img": block(args["img"], args["t_emb"], args["mod_segments"],
                                         args["rope_freqs"],
                                         transformer_options=args["transformer_options"])}
                h = blocks_replace[("double_block", i)](
                    {"img": h, "t_emb": t_emb, "mod_segments": mod_segments,
                     "rope_freqs": rope_freqs,
                     "transformer_options": transformer_options},
                    {"original_block": block_wrap})["img"]
            else:
                h = block(h, t_emb, mod_segments, rope_freqs,
                          transformer_options=transformer_options)
        if prefetch_queue is not None:
            comfy.model_prefetch.prefetch_queue_pop(prefetch_queue, device, None)

        # target streams are single contiguous segments (audio then video, last two)
        va, vb, _ = next(s for s in layout.segments if s[2] == "video")
        aa, ab, _ = next(s for s in layout.segments if s[2] == "audio")
        if video_rows_t is not None:
            video_seg = (va, vb, rows_to_mod_index(video_rows_t, 0) // 3)
        else:
            video_seg = (va, vb, t_row[seg_t["video"]])
        if audio_rows_t is not None:
            audio_seg = (aa, ab, rows_to_mod_index(audio_rows_t, 0) // 3)
        else:
            audio_seg = (aa, ab, t_row[seg_t["audio"]])
        v, a = self.final_layer(h, t_emb, video_seg, audio_seg)

        video_out = h3m.unpatchify_video(v, latent_t, lat_h // 2, lat_w // 2,
                                         self.latents_dim, self.patch_size)
        video_out = video_out[:, :, :orig_t, :orig_h, :orig_w]
        audio_out = h3m.unpack_audio(a)

        return [-video_out.to(video_x.dtype), -audio_out.to(audio_x.dtype)]

    return {
        "mask_row_values": mask_row_values,
        "_mod_row": _mod_row,
        "_mod_scale_shift": _mod_scale_shift,
        "_mod_gate": _mod_gate,
        "final_forward": final_forward,
        "forward": forward,
        "_forward": _forward,
    }


def _install_engine_compat(h3m):
    # Add/replace the MiniMax-H3 diffusion-model helpers and methods from PR #15375.
    fns = _engine_functions(h3m)
    for name in ("mask_row_values", "_mod_row", "_mod_scale_shift", "_mod_gate"):
        setattr(h3m, name, fns[name])
    h3m.FinalLayer.forward = fns["final_forward"]
    h3m.MiniMaxH3Model.forward = fns["forward"]
    h3m.MiniMaxH3Model._forward = fns["_forward"]
    for fn in fns.values():
        _mark(fn)


def _model_base_hooks(model_base):
    """process_denoise_mask / scale_latent_inpaint from PR #15375, bound to
    ``model_base`` the same way the engine functions are bound to h3m."""

    def process_denoise_mask(self, denoise_masks):
        # snap the video mask to the DiT patch grid (2x2 latent pixels per patch)
        # and the audio mask to each audio latent frame (which run at 40 audio frames per second)
        torch = model_base.torch
        video_mask = denoise_masks[0]
        h, w = video_mask.shape[-2:]
        ph, pw = self.diffusion_model.patch_size[1:]
        lead = video_mask.shape[:-2]
        video_mask = torch.nn.functional.pad(
            video_mask.reshape((-1,) + video_mask.shape[-3:]),
            (0, -w % pw, 0, -h % ph), mode="replicate")
        video_mask = video_mask.reshape(lead + video_mask.shape[-2:])
        video_mask = video_mask.reshape(
            video_mask.shape[:-2] + (video_mask.shape[-2] // ph, ph,
                                     video_mask.shape[-1] // pw, pw)
        ).amax(dim=(-3, -1))
        # quantize to 1/256 so a gradient mask yields a bounded set of row timesteps
        video_mask = torch.round(video_mask * 256.0) / 256.0
        # threshold values above 0.995 to 1.0 and values below 0.05 to 0.0
        video_mask = video_mask.masked_fill(video_mask >= 0.995, 1.0).masked_fill(
            video_mask <= 0.05, 0.0)
        denoise_masks[0] = video_mask.repeat_interleave(ph, dim=-2).repeat_interleave(
            pw, dim=-1)[..., :h, :w]
        if len(denoise_masks) > 1:
            audio_mask = denoise_masks[1].amax(dim=1, keepdim=True)
            audio_mask = torch.round(audio_mask * 256.0) / 256.0
            audio_mask = audio_mask.masked_fill(audio_mask >= 0.995, 1.0).masked_fill(
                audio_mask <= 0.05, 0.0)
            denoise_masks[1] = audio_mask.expand_as(denoise_masks[1]).contiguous()
        return denoise_masks

    def scale_latent_inpaint(self, sigma, noise, latent_image, **kwargs):
        # preserved regions run at the cond timestep, inject them at cond strength
        utils = model_base.utils
        comfy = model_base.comfy
        shapes = self.latent_shapes
        if shapes is None or len(shapes) < 2:
            return super(model_base.MiniMaxH3, self).scale_latent_inpaint(
                sigma=sigma, noise=noise, latent_image=latent_image, **kwargs)
        cleans = utils.unpack_latents(latent_image, shapes)
        noises = utils.unpack_latents(noise, shapes)
        aug = comfy.ldm.minimax.model.VISUAL_COND_TIMESTEP  # H3's video timestep is 0.999 by default
        cleans[0] = aug * cleans[0] + (1.0 - aug) * noises[0]
        scale = self.audio_scale()
        if scale != 1.0:
            # the sampler carries audio as (sigma_v / sigma_a) * x_audio and latent_image
            # holds audio_scale * x_audio, so rescale for the model to see it clean
            model_sampling = self.model_sampling
            sigma_v = sigma.clamp(min=1e-6)
            sigma_a = comfy.ldm.minimax.model.time_shift_sigma(
                sigma_v, model_sampling.shift, model_sampling.audio_shift)
            factor = (sigma_v / sigma_a) / scale
            cleans[1] = cleans[1] * factor.view(
                factor.shape[:1] + (1,) * (cleans[1].ndim - 1)).to(cleans[1].dtype)
        return utils.pack_latents(cleans)[0]

    _mark(process_denoise_mask)
    _mark(scale_latent_inpaint)
    return process_denoise_mask, scale_latent_inpaint


def ensure_h3_mask_compat():
    """Install only #15375 capabilities missing from the live ComfyUI build."""
    import comfy.model_base as model_base
    import comfy.ldm.minimax.model as h3m

    cls = getattr(model_base, "MiniMaxH3", None)
    if cls is None:
        raise RuntimeError("h3_suite: MiniMaxH3 model class not found")

    before = capability_status()

    # The diffusion mask engine is a coupled group. If a future ComfyUI exposes
    # only part of that group, do not mix a snapshot with unknown half-native
    # behavior; fail loudly so the compatibility layer can be updated safely.
    indicators = before["mask_engine_indicators"]
    characteristic = [
        indicators["mask_row_values"],
        indicators["mod_row"],
        indicators["forward_masks"],
        indicators["inner_masks"],
    ]
    partial_native = any(characteristic) and not all(characteristic)
    if partial_native and not before["mask_engine_compat"]:
        raise RuntimeError(
            "h3_suite: partial native H3 AV-mask engine detected. "
            "Refusing to combine an old compatibility snapshot with a partially "
            "updated ComfyUI. Update this node pack or ComfyUI."
        )

    if not before["mask_engine_complete"]:
        _install_engine_compat(h3m)
        _LOG.info("h3_suite: PR #15375 diffusion-mask compatibility enabled")

    # Build both hook functions once, then install only missing subclass
    # overrides. This keeps native implementations authoritative.
    need_process = not (
        "process_denoise_mask" in cls.__dict__
        and callable(getattr(cls, "process_denoise_mask", None))
    )
    need_scale = not (
        "scale_latent_inpaint" in cls.__dict__
        and callable(getattr(cls, "scale_latent_inpaint", None))
    )
    if need_process or need_scale:
        process_fn, scale_fn = _model_base_hooks(model_base)
        if need_process:
            cls.process_denoise_mask = process_fn
            _LOG.info("h3_suite: PR #15375 mask preprocessing compatibility enabled")
        if need_scale:
            cls.scale_latent_inpaint = scale_fn
            _LOG.info("h3_suite: PR #15375 inpaint scaling compatibility enabled")

    after = capability_status()
    ready = (
        after["mask_engine_complete"]
        and (
            after["process_denoise_mask_native"]
            or after["process_denoise_mask_compat"]
        )
        and (
            after["scale_latent_inpaint_native"]
            or after["scale_latent_inpaint_compat"]
        )
    )
    if not ready:
        raise RuntimeError(
            "h3_suite: H3 AV-mask compatibility is incomplete after patching"
        )
    return True


def is_ready():
    try:
        s = capability_status()
    except Exception:
        return False
    return bool(
        s["mask_engine_complete"]
        and (s["process_denoise_mask_native"] or s["process_denoise_mask_compat"])
        and (s["scale_latent_inpaint_native"] or s["scale_latent_inpaint_compat"])
    )
