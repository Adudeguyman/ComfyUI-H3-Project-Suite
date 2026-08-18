"""Lift MiniMax H3's first/last-only keyframe anchor restriction.

Stock ComfyUI builds keyframe conditioning rows at one of two time
coordinates and rejects everything else:

    if pixel_index == 0:
        cond_t = float(text_len)
    elif frame_count is not None and pixel_index == frame_count - 1:
        cond_t = float(text_len) + sum(_video_t_spans(latent_t)) - FRAME_RESCALE
    else:
        raise ValueError("only first/last keyframe anchors are supported")

Both branches are the same expression. Each video token spans
FRAME_RESCALE * FRAME_PER_TOKEN[k % 5] and covers FRAME_PER_TOKEN[k % 5]
pixel frames, so the cumulative time at pixel frame p is exactly
FRAME_RESCALE * p, for every p. Substituting p = frame_count - 1
reproduces the second branch identically:

    text_len + FRAME_RESCALE * (frame_count - 1)
      == text_len + FRAME_RESCALE * frame_count - FRAME_RESCALE
      == text_len + sum(_video_t_spans(latent_t)) - FRAME_RESCALE

So the general position is:

    cond_t = text_len + FRAME_RESCALE * pixel_index

We do NOT rewrite the source of PackedLayout.__init__. Instead every
keyframe is handed to stock code with resolved_frame_index = 0, which is
always legal, and the real index rides along under MC_KEY. After the
stock constructor returns we rewrite the time column of each cond
segment's rows in position_ids. RoPE is built at forward time from
position_ids, so this lands before anything reads it.

That keeps the patch surface to one attribute we can verify rather than a
copy of a 90-line constructor that would rot on the next ComfyUI change.
"""

import logging

import torch

import comfy.ldm.minimax.model as mm

# Packs in this lineage all lift the same first/last restriction and all
# smuggle the real index under MC_KEY, so two of them patching the same
# constructor would apply the same correction twice and shift every
# anchor. Each pack marks its own wrapper; seeing ANY of these means
# somebody already owns the constructor and does the same job we would.
LAYOUT_MARKER = "_h3_project_suite_layout_patch"
KNOWN_LAYOUT_MARKERS = (
    LAYOUT_MARKER,
    "_h3_motion_context_layout_patch",   # NikoDemon80/ComfyUI-H3-Motion-Context
)

MC_KEY = "motion_context_index"
MC_AUDIO_KEY = "motion_context_audio_end_frame"
_LOG = logging.getLogger("h3_suite")

_orig_init = None
_applied = False
_mode = None      # "full" | "audio_only" once applied
_deferred_to = None   # set when somebody else already covers this


def _ref_cursor_advance(refs):
    """How far ref blocks push the target origin past text_len.

    Refs are laid out sequentially from a cursor that starts at text_len,
    and the target audio and video rows use the cursor's final value as
    their origin. Keyframe coordinates are computed from text_len directly,
    so without this term adding any ref would slide the anchors backwards
    relative to the clip they are anchoring.
    """
    if not refs:
        return 0.0
    cursor = 0.0
    for blk in refs:
        kind = blk.get("kind")
        if kind == "image":
            cursor += 1.0
        elif kind == "audio":
            cursor += float(blk.get("ref_audio_t", 0))
        elif kind in ("video", "video_audio"):
            rt = float(blk.get("ref_audio_t", 0))
            vt = int(blk.get("latent_t", 0))
            if not rt and not vt:
                raise RuntimeError(
                    "h3_suite: %r ref carries neither ref_audio_t nor "
                    "latent_t, so its cursor advance is unknowable. Every "
                    "coordinate after it would be wrong; refusing to guess."
                    % kind)
            cursor += max(rt, sum(mm._video_t_spans(vt)))
        else:
            # silently contributing 0 for an unrecognised kind would slide
            # every later ref, the pinned audio window and the keyframe
            # anchors by that block's real width -- a seam that drifts for
            # no visible reason. Fail where the cause is still legible.
            raise RuntimeError(
                "h3_suite: unrecognised ref kind %r in the "
                "conditioning. Cannot compute the layout cursor." % kind)
    return cursor


def _cond_t(text_len, latent_t, frame_count, p):
    """Time coordinate for a keyframe anchored at pixel frame p.

    The endpoints reuse stock's exact expressions rather than the general
    formula. They are mathematically identical, but stock accumulates
    latent_t float additions where the general form does one multiply, and
    those differ in the last bits (about 7e-15). Matching stock bit for bit
    means an existing first/last graph builds byte-identical positions
    after this patch is applied, and lets the self-test stay strict.
    """
    if p == 0:
        return float(text_len)
    if frame_count is not None and p == frame_count - 1:
        return float(text_len) + sum(mm._video_t_spans(latent_t)) - mm.FRAME_RESCALE
    return float(text_len) + mm.FRAME_RESCALE * float(p)


def _fixup(layout, text_len, latent_t, frame_count, keyframes, refs=None):
    """Rewrite cond-row time coordinates to the general position formula."""
    offset = _ref_cursor_advance(refs)
    if offset and any(kf.get(MC_KEY) is None for kf in keyframes):
        # keyframes without MC_KEY are left exactly as stock built them,
        # which means they do NOT get the ref cursor compensation. Mixing
        # them with MC keyframes under a ref would slide the stock anchors
        # relative to ours and to the target. Nothing produces this today;
        # refuse loudly in case something ever does.
        raise RuntimeError(
            "h3_suite: stock and motion-context keyframes mixed in "
            "one graph alongside a ref; their coordinates would disagree. "
            "Give every keyframe a %s entry or remove the refs." % MC_KEY)
    cond_spans = [(a, b) for a, b, kind in layout.segments if kind == "cond"]
    if len(cond_spans) != len(keyframes):
        raise RuntimeError(
            "h3_suite: expected %d cond segments, layout has %d. "
            "Refusing to rewrite positions."
            % (len(keyframes), len(cond_spans)))
    for (a, b), kf in zip(cond_spans, keyframes):
        p = kf.get(MC_KEY)
        if p is None:
            continue
        layout.position_ids[a:b, 0] = _cond_t(text_len, latent_t, frame_count, p) + offset


def _fixup_audio(layout, text_len, refs):
    """Move the marked audio ref rows onto the target timeline.

    Refs and keyframes carry identical row machinery; what makes the model
    read a ref as "a separate clip to imitate" rather than "this clip,
    continued" is that its coordinates sit in a span before the target.
    That distinction decided continuation vs reproduction for video, and
    seam analysis showed the audio ref producing phase-unlocked imitation.
    So: keep the audio on the ref path for construction and payload (rows
    built, latents filled, all stock code untouched) and TRANSLATE its
    time coordinates so the window END lands at target frame
    MC_AUDIO_KEY -- the same instant the pinned video ends.

    Translation, not per-row assignment: new = old + shift preserves
    whatever intra-block structure stock built (row order, the rows-per-
    step factor, fractional offsets), so nothing about the block's
    internals is assumed.

    The ref still advances the layout cursor, so its old coordinate slot is
    left VACANT after the move. An audio
    window longer than the video window therefore spills backwards into
    empty coordinate space rather than onto the text rows -- the collision
    that made `before` mode fail for video does not arise here.

    Row selection is by coordinate range (the vacated slot), excluding
    cond segments explicitly so a stock first-frame keyframe sitting at
    text_len can never be swept up regardless of fixup order.

    Multi-ref support here is based on the compatibility patch contributed by
    seitanism in the Banodoco MiniMax H3 seamless-extension thread.
    Ordinary Ref2VA blocks may precede the marked Motion Context audio ref.
    The marked block must be last: the node appends it after the incoming
    conditioning refs, and this gives the target and keyframes one common
    final cursor origin.
    """
    marked_idx = [i for i, r in enumerate(refs)
                  if r.get(MC_AUDIO_KEY) is not None]
    if len(marked_idx) != 1:
        raise RuntimeError(
            "h3_suite: audio timeline placement requires exactly one "
            "ref marked with %s; layout has %d refs, %d marked."
            % (MC_AUDIO_KEY, len(refs), len(marked_idx)))
    idx = marked_idx[0]
    if idx != len(refs) - 1:
        raise RuntimeError(
            "h3_suite: the marked Motion Context audio ref must be "
            "the last ref block. Apply Motion Context after the stock MiniMax "
            "H3 conditioning node so existing refs are preserved first.")
    blk = refs[idx]
    if blk.get("kind") != "audio":
        raise RuntimeError(
            "h3_suite: %s set on a %r ref; only audio refs can be "
            "moved onto the timeline." % (MC_AUDIO_KEY, blk.get("kind")))
    rt = int(blk.get("ref_audio_t", 0))
    if rt <= 0:
        return
    prefix = _ref_cursor_advance(refs[:idx])
    slot_start = float(text_len) + prefix
    slot_end = slot_start + float(rt)
    target_origin = float(text_len) + _ref_cursor_advance(refs)
    end_frame = float(blk[MC_AUDIO_KEY])

    t = layout.position_ids[:, 0]
    sel = (t >= slot_start - 1e-4) & (t < slot_end - 1e-4)
    for a, b, kind in layout.segments:
        if kind == "cond":
            sel[a:b] = False
    count = int(sel.sum())
    if count < rt or count > 8 * rt:
        raise RuntimeError(
            "h3_suite: found %d rows in the marked audio ref slot "
            "[%.4f, %.4f) for %d latent steps, expected between %d and %d. "
            "Upstream layout change or overlapping coordinates; refusing to "
            "move audio rows."
            % (count, slot_start, slot_end, rt, rt, 8 * rt))
    # window end at target time FRAME_RESCALE * end_frame, width rt steps
    desired_start = target_origin + mm.FRAME_RESCALE * end_frame - float(rt)
    shift = desired_start - slot_start
    layout.position_ids[sel, 0] = t[sel] + shift


def _patched_init(self, text_len, latent_t, latent_h, latent_w, audio_t,
                  keyframes=None, refs=None, frame_count=None):
    if _mode == "audio_only" and keyframes:
        # this core places anchors at their real coordinates natively, so
        # UN-SMUGGLE: the pack's blocks ride at resolved_frame_index 0
        # with the true position under MC_KEY (the pre-#15439 convention,
        # when interior values raised). Handing core index 0 would stack
        # every pinned block on the first frame - which reads as a
        # two-frame skip at every join. Hand it the truth instead.
        fixed = []
        for kf in keyframes:
            real = kf.get(MC_KEY)
            if real is not None:
                kf = dict(kf)
                kf["resolved_frame_index"] = int(real)
            fixed.append(kf)
        keyframes = fixed
    # forward frame_count only when the thing underneath takes it: another
    # pack may have replaced it with a narrower signature
    kw = {"keyframes": keyframes, "refs": refs}
    if _accepts(_orig_init, "frame_count"):
        kw["frame_count"] = frame_count
    _orig_init(self, text_len, latent_t, latent_h, latent_w, audio_t, **kw)
    has_mc_kf = bool(keyframes) and any(
        kf.get(MC_KEY) is not None for kf in keyframes)
    has_mc_audio = bool(refs) and any(
        r.get(MC_AUDIO_KEY) is not None for r in refs)
    if has_mc_kf and _mode != "audio_only":
        # in audio_only mode core has already placed every anchor at its
        # own coordinate; repositioning them again would double-shift
        _fixup(self, text_len, latent_t, frame_count, keyframes, refs)
    if has_mc_audio:
        _fixup_audio(self, text_len, refs)
    # neither marked: stock graph, leave it exactly as built


def _self_test():
    """Prove the rewrite reproduces stock positions before committing.

    Builds the two anchors stock code already supports, once the stock way
    and once through our mechanism, and requires the position tensors to
    match exactly. If ComfyUI changes the position maths underneath us this
    fails and the patch is not applied.
    """
    text_len, latent_t, lh, lw, audio_t = 7, 7, 22, 38, 16
    frame_count = sum(mm.FRAME_PER_TOKEN[k % 5] for k in range(latent_t))
    # a third-party patch may have narrowed the signature; build the extra
    # kwargs once so every call below agrees with what is installed
    _fc = {"frame_count": frame_count} if _accepts(_orig_init,
                                                   "frame_count") else {}

    stock_kf = [{"resolved_frame_index": 0},
                {"resolved_frame_index": frame_count - 1}]
    ours_kf = [{"resolved_frame_index": 0, MC_KEY: 0},
               {"resolved_frame_index": 0, MC_KEY: frame_count - 1}]

    a = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(a, text_len, latent_t, lh, lw, audio_t,
               keyframes=stock_kf, **_fc)

    b = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(b, text_len, latent_t, lh, lw, audio_t,
               keyframes=ours_kf, **_fc)
    _fixup(b, text_len, latent_t, frame_count, ours_kf)

    if a.position_ids.shape != b.position_ids.shape:
        raise RuntimeError("position_ids shape mismatch in self-test")
    if not torch.equal(a.position_ids, b.position_ids):
        bad = (a.position_ids != b.position_ids).any(dim=1).nonzero().flatten()
        raise RuntimeError("position mismatch at rows %s" % bad[:8].tolist())

    # a consecutive run must land on strictly increasing coordinates inside
    # the span the two endpoints define
    run = [{"resolved_frame_index": 0, MC_KEY: i} for i in range(4)]
    c = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(c, text_len, latent_t, lh, lw, audio_t,
               keyframes=run, **_fc)
    _fixup(c, text_len, latent_t, frame_count, run)
    ts = [float(c.position_ids[s, 0]) for s, _, k in c.segments if k == "cond"]
    if len(ts) != len(run):
        raise RuntimeError("expected %d cond segments, got %d" % (len(run), len(ts)))
    if any(ts[i] >= ts[i + 1] for i in range(len(ts) - 1)):
        raise RuntimeError("consecutive anchors not strictly increasing: %s" % ts)
    t_last = float(text_len) + mm.FRAME_RESCALE * (frame_count - 1)
    if not (ts[0] == float(text_len) and ts[-1] < t_last):
        raise RuntimeError("run %s escapes the [%.4f, %.4f] span"
                           % (ts, float(text_len), t_last))

    # adding a ref must not move the anchors relative to the target. Stock
    # cond rows cannot be the reference here: stock computes them from
    # text_len and never compensates for refs, which is the very bug
    # _ref_cursor_advance exists to fix. The ground truth is the target
    # rows themselves. Ref rows are laid out BEFORE the target, so the
    # largest time coordinate in position_ids belongs to the end of the
    # target in both layouts, and the anchor-to-end gap must be identical
    # with and without the ref. This exercises _ref_cursor_advance against
    # stock's real cursor arithmetic, so if upstream changes how refs
    # advance the cursor, this fails and the patch is not applied.
    ref = [{"kind": "audio", "ref_audio_t": 8}]
    d = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(d, text_len, latent_t, lh, lw, audio_t,
               keyframes=run, refs=ref, **_fc)
    _fixup(d, text_len, latent_t, frame_count, run, refs=ref)
    ts_ref = [float(d.position_ids[s, 0]) for s, _, k in d.segments if k == "cond"]
    if len(ts_ref) != len(ts):
        raise RuntimeError("cond segment count changed when a ref was added")
    # a semantic failure here is a shift of whole rows (the 8.0 of the ref,
    # or FRAME_RESCALE multiples), while legitimate noise is float
    # accumulation from a different origin, orders of magnitude below 1e-3
    # even at float32. Strict equality stays reserved for the endpoint test.
    tol = 1e-3
    gap = float(c.position_ids[:, 0].max()) - ts[0]
    gap_ref = float(d.position_ids[:, 0].max()) - ts_ref[0]
    if abs(gap - gap_ref) > tol:
        raise RuntimeError(
            "ref compensation off by %.6f: anchor-to-target gap %.6f without "
            "ref, %.6f with. _ref_cursor_advance no longer matches the "
            "layout's cursor arithmetic." % (gap_ref - gap, gap, gap_ref))
    shifts = [b - a for a, b in zip(ts, ts_ref)]
    if any(abs(s - shifts[0]) > tol for s in shifts):
        raise RuntimeError("ref shifted anchors unevenly: %s" % shifts)

    # audio timeline placement: rebuild layout d with the ref marked and
    # require that exactly the rows in the ref's coordinate slot moved,
    # all by one uniform shift, with every other row bit-identical.
    end_frame = 4
    rt = 8
    ref_mc = [{"kind": "audio", "ref_audio_t": rt, MC_AUDIO_KEY: end_frame}]
    e = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(e, text_len, latent_t, lh, lw, audio_t,
               keyframes=run, refs=ref_mc, **_fc)
    _fixup(e, text_len, latent_t, frame_count, run, refs=ref_mc)
    _fixup_audio(e, text_len, ref_mc)
    if e.position_ids.shape != d.position_ids.shape:
        raise RuntimeError("audio move changed the layout shape")
    if not torch.equal(d.position_ids[:, 1:], e.position_ids[:, 1:]):
        raise RuntimeError("audio move touched a non-time coordinate column")
    td, te = d.position_ids[:, 0], e.position_ids[:, 0]
    cond_rows = set()
    for a, b, kind in d.segments:
        if kind == "cond":
            cond_rows.update(range(a, b))
    expect_moved = set(i for i in range(len(td))
                       if text_len - 1e-4 <= float(td[i]) < text_len + rt - 1e-4
                       and i not in cond_rows)
    moved = set(i for i in range(len(td)) if float(td[i]) != float(te[i]))
    if moved != expect_moved:
        raise RuntimeError(
            "audio move touched the wrong rows: %d moved, %d expected, "
            "e.g. %s" % (len(moved), len(expect_moved),
                         sorted(moved ^ expect_moved)[:8]))
    if not moved:
        raise RuntimeError("audio move moved no rows")
    want_shift = mm.FRAME_RESCALE * end_frame  # advance == rt cancels here
    deltas = [float(te[i]) - float(td[i]) for i in sorted(moved)]
    if any(abs(dd - want_shift) > 1e-5 for dd in deltas):
        raise RuntimeError("audio rows shifted non-uniformly or by the wrong "
                           "amount: %s vs %.6f" % (deltas[:4], want_shift))

    # Ref2VA compatibility: ordinary image and audio refs remain byte-identical
    # while the final marked Motion Context audio block moves onto the target
    # timeline. The extra ordinary audio block covers the optional full-clip
    # audio reference used by Ref2VA workflows.
    img1 = {"kind": "image", "latent_h": lh, "latent_w": lw}
    img2 = {"kind": "image", "latent_h": lh, "latent_w": lw}
    existing_audio = {"kind": "audio", "ref_audio_t": 3}
    audio_plain = {"kind": "audio", "ref_audio_t": rt}
    audio_marked = {"kind": "audio", "ref_audio_t": rt,
                    MC_AUDIO_KEY: end_frame}
    refs_plain = [img1, img2, existing_audio, audio_plain]
    refs_marked = [img1, img2, existing_audio, audio_marked]

    f = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(f, text_len, latent_t, lh, lw, audio_t,
               keyframes=run, refs=refs_plain, **_fc)
    _fixup(f, text_len, latent_t, frame_count, run, refs=refs_plain)

    g = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(g, text_len, latent_t, lh, lw, audio_t,
               keyframes=run, refs=refs_marked, **_fc)
    _fixup(g, text_len, latent_t, frame_count, run, refs=refs_marked)
    _fixup_audio(g, text_len, refs_marked)

    if g.position_ids.shape != f.position_ids.shape:
        raise RuntimeError("multi-ref audio move changed the layout shape")
    if not torch.equal(f.position_ids[:, 1:], g.position_ids[:, 1:]):
        raise RuntimeError("multi-ref audio move touched a non-time coordinate")

    tf, tg = f.position_ids[:, 0], g.position_ids[:, 0]
    prefix = _ref_cursor_advance(refs_plain[:-1])
    slot_start = float(text_len) + prefix
    slot_end = slot_start + float(rt)
    cond_rows = set()
    for a, b, kind in f.segments:
        if kind == "cond":
            cond_rows.update(range(a, b))
    expect_moved = set(
        i for i in range(len(tf))
        if slot_start - 1e-4 <= float(tf[i]) < slot_end - 1e-4
        and i not in cond_rows)
    moved = set(i for i in range(len(tf)) if float(tf[i]) != float(tg[i]))
    if moved != expect_moved:
        raise RuntimeError(
            "multi-ref audio move touched the wrong rows: %d moved, %d "
            "expected, e.g. %s" %
            (len(moved), len(expect_moved), sorted(moved ^ expect_moved)[:8]))
    if not moved:
        raise RuntimeError("multi-ref audio move moved no rows")

    target_origin = float(text_len) + _ref_cursor_advance(refs_marked)
    desired_start = target_origin + mm.FRAME_RESCALE * end_frame - float(rt)
    want_multi_shift = desired_start - slot_start
    multi_deltas = [float(tg[i]) - float(tf[i]) for i in sorted(moved)]
    if any(abs(dd - want_multi_shift) > 1e-5 for dd in multi_deltas):
        raise RuntimeError(
            "multi-ref audio rows shifted non-uniformly or by the wrong "
            "amount: %s vs %.6f" % (multi_deltas[:4], want_multi_shift))


def _init_owner(fn=None):
    """Describe whoever currently owns PackedLayout.__init__.

    Another custom node may have wrapped it before we look. When that
    wrapper has a different signature, everything downstream fails in a
    way that reads like a ComfyUI change, so name the real owner rather
    than leaving the user to guess between packs.
    """
    fn = fn or mm.PackedLayout.__init__
    mod = getattr(fn, "__module__", None) or "?"
    qual = getattr(fn, "__qualname__", getattr(fn, "__name__", "?"))
    stock = mod == getattr(mm, "__name__", "comfy.ldm.minimax.model")
    return mod, qual, stock


def _accepts(fn, name):
    """Does this callable take a keyword argument called `name`?"""
    try:
        import inspect
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True   # unintrospectable: assume yes, the call will tell us
    for p in sig.parameters.values():
        if p.kind is p.VAR_KEYWORD:
            return True
        if p.name == name:
            return True
    return False


def _core_handles_interior_anchors():
    """Does this ComfyUI already place an interior anchor correctly?

    PR #15439 merged upstream on 2026-08-13 and does what this module
    does: it removes the first/last restriction. On such a core our
    rewrite is not merely unnecessary, it would shift positions core has
    already placed. Detection is behavioural rather than a version check,
    because a version string cannot tell us whether a given build carries
    the change - the 0.33.0 tag does not, but a master checkout calling
    itself 0.33.0 does.

    Returns True only when core ACCEPTS three anchors and lands them in
    strictly increasing order. Anything else returns False and we patch
    as before.
    """
    text_len, latent_t, lh, lw, audio_t = 7, 7, 22, 38, 16
    try:
        frame_count = sum(mm.FRAME_PER_TOKEN[k % 5] for k in range(latent_t))
        mid = frame_count // 2

        def kf(idx):
            # a keyframe only produces cond rows when it carries a latent:
            # master sizes the block from the latent's own temporal extent
            # instead of assuming a single frame, so a bare index dict
            # yields no rows and would read as a failure to place it
            return {"resolved_frame_index": idx,
                    "latent": torch.zeros(1, 16, 1, lh, lw)}

        probe = mm.PackedLayout.__new__(mm.PackedLayout)
        kw = {"keyframes": [kf(0), kf(mid), kf(frame_count - 1)]}
        if _accepts(mm.PackedLayout.__init__, "frame_count"):
            kw["frame_count"] = frame_count
        mm.PackedLayout.__init__(probe, text_len, latent_t, lh, lw, audio_t,
                                 **kw)
        ts = [float(probe.position_ids[a, 0])
              for a, _b, kind in probe.segments if kind == "cond"]
        if len(ts) != 3:
            return False
        return ts[0] < ts[1] < ts[2]
    except Exception as exc:
        _LOG.debug("h3_suite: interior-anchor probe inconclusive (%s)", exc)
        return False


def _audio_self_test():
    """Prove the audio timeline translation on THIS core, alone.

    On a core with PR #15439 the full self-test does not apply - it
    verifies our video-anchor rewrite against old-core behaviour. But the
    audio translation is this pack's own mechanism regardless of core,
    so it gets its own proof: build one layout with a marked audio ref,
    translate it, and require exactly the ref's rows to have moved, all
    by one uniform shift, everything else bit-identical.
    """
    import torch
    text_len, latent_t, lh, lw, audio_t = 7, 7, 22, 38, 16
    end_frame, rt = 4, 8
    kf = [{"resolved_frame_index": 0,
           "latent": torch.zeros(1, 16, 1, lh, lw)}]
    ref_mc = [{"kind": "audio", "ref_audio_t": rt, MC_AUDIO_KEY: end_frame}]
    kw = {}
    if _accepts(_orig_init, "frame_count"):
        kw["frame_count"] = sum(mm.FRAME_PER_TOKEN[k % 5]
                                for k in range(latent_t))
    d = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(d, text_len, latent_t, lh, lw, audio_t,
               keyframes=kf, refs=ref_mc, **kw)
    e = mm.PackedLayout.__new__(mm.PackedLayout)
    _orig_init(e, text_len, latent_t, lh, lw, audio_t,
               keyframes=kf, refs=ref_mc, **kw)
    _fixup_audio(e, text_len, ref_mc)
    if e.position_ids.shape != d.position_ids.shape:
        raise RuntimeError("audio move changed the layout shape")
    if not torch.equal(d.position_ids[:, 1:], e.position_ids[:, 1:]):
        raise RuntimeError("audio move touched a non-time column")
    td, te = d.position_ids[:, 0], e.position_ids[:, 0]
    moved = [i for i in range(len(td)) if float(td[i]) != float(te[i])]
    if not moved:
        raise RuntimeError("audio move moved no rows")
    deltas = {round(float(te[i]) - float(td[i]), 5) for i in moved}
    if len(deltas) != 1:
        raise RuntimeError("audio rows shifted non-uniformly: %s"
                           % sorted(deltas))
    audio_rows = set()
    for a, b, kind in d.segments:
        if kind in ("ref_audio", "audio_ref", "cond_audio", "ref"):
            audio_rows.update(range(a, b))
    stray = [i for i in moved if audio_rows and i not in audio_rows]
    if audio_rows and stray:
        raise RuntimeError("audio move touched non-audio rows: %s"
                           % stray[:6])


def _owned_by_a_sibling():
    """Has another pack in this lineage already patched the layout?

    They mark their wrapper, and they all use the same MC_KEY smuggling
    convention, so whichever one installed first already places our
    keyframes correctly. Patching on top would apply the correction
    twice. Returns the marker found, or None.
    """
    init = getattr(getattr(mm, "PackedLayout", None), "__init__", None)
    if init is None:
        return None
    for marker in KNOWN_LAYOUT_MARKERS:
        if getattr(init, marker, False):
            return marker
    # older copies in this family predate markers but are all named the
    # same way, a habit inherited from the shared ancestor
    if getattr(init, "__name__", "") == "_patched_init" and not _applied:
        return "an unmarked copy of this patch"
    return None


def apply_patch():
    global _orig_init, _applied, _mode
    if _applied:
        return True
    if not hasattr(mm, "PackedLayout") or not hasattr(mm, "FRAME_RESCALE"):
        _LOG.warning("h3_suite: MiniMax H3 model module missing expected "
                     "attributes, patch not applied")
        return False
    sibling = _owned_by_a_sibling()
    if sibling:
        global _deferred_to
        _deferred_to = sibling
        _LOG.info("h3_suite: another H3 motion-context pack already owns "
                  "ComfyUI's layout (%s). It uses the same keyframe "
                  "convention we do, so it places our anchors correctly - "
                  "leaving it alone rather than correcting twice.", sibling)
        return False
    if _core_handles_interior_anchors():
        # core owns the video anchors now, but the audio timeline
        # translation was never part of the merged PR - that mechanism is
        # this pack's own and still has to run
        _orig_init = mm.PackedLayout.__init__
        try:
            _audio_self_test()
        except Exception as exc:
            _orig_init = None
            _LOG.warning("h3_suite: audio-timeline self-test failed on this "
                         "core (%s); pinned audio would sit at the ref "
                         "position instead of the timeline end.", exc)
            return False
        setattr(_patched_init, LAYOUT_MARKER, True)
        mm.PackedLayout.__init__ = _patched_init
        _mode = "audio_only"
        _applied = True
        _LOG.info("h3_suite: video keyframe anchors are core's own (PR "
                  "#15439 merged); keeping only the audio timeline "
                  "placement, which was never upstreamed")
        return True
    _orig_init = mm.PackedLayout.__init__
    mod, qual, stock = _init_owner(_orig_init)
    if not stock:
        _LOG.warning(
            "h3_suite: PackedLayout.__init__ is not ComfyUI's own - it is "
            "%s from %s. Another custom node has patched it. Ours builds on "
            "top of whatever is installed, so if the self-test below fails, "
            "that pack is the thing to disable first.", qual, mod)
    if not _accepts(_orig_init, "frame_count"):
        _LOG.warning(
            "h3_suite: the installed PackedLayout.__init__ (%s from %s) does "
            "not accept frame_count, which stock ComfyUI 0.31-0.33 does. "
            "This is a third-party patch with a narrower signature; the "
            "last-frame anchor cannot be verified against it.", qual, mod)
    try:
        _self_test()
    except Exception as exc:
        _orig_init = None
        _LOG.warning("h3_suite: self-test failed (%s), patch not applied. "
                     "Interior keyframe anchors unavailable.", exc)
        return False
    setattr(_patched_init, LAYOUT_MARKER, True)
    mm.PackedLayout.__init__ = _patched_init
    _mode = "full"
    _applied = True
    _LOG.info("h3_suite: interior keyframe anchors enabled")
    return True


def is_applied():
    return _applied


def is_covered():
    """Are interior anchors going to be placed correctly, by anyone?

    True when we patched, when the core does it natively, or when another
    pack in this lineage already owns the constructor. The node needs
    this rather than is_applied(): refusing to render because a sibling
    pack got there first would be worse than the conflict.
    """
    return bool(_applied or _deferred_to)
