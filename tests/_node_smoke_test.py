"""Smoke test: run H3Context.apply() end to end with fakes.

Fakes ComfyUI's modules and tensor ops (numpy-backed) and drives the node
exactly as a graph would: a 124-frame clip at 480x864, 22 context frames,
audio from the previous clip's LATENT. Starts with Ref2VA image/video refs and
checks that they survive ahead of the appended Motion Context audio ref, plus
the keyframe count and indices, audio step count, and fractional end_frame
carrying the grid-overhang compensation.
"""

import sys
import types

import numpy as np

import os
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TESTS_DIR)  # repo root, where the package lives
sys.path.insert(0, _TESTS_DIR)
from _mock_harness import make_mm, make_torch  # noqa: E402


class T:
    """Minimal numpy-backed tensor stand-in."""

    def __init__(self, a):
        self.a = np.asarray(a)

    @property
    def shape(self):
        return self.a.shape

    @property
    def ndim(self):
        return self.a.ndim

    def __getitem__(self, idx):
        return T(self.a[idx])

    def movedim(self, src, dst):
        return T(np.moveaxis(self.a, src, dst))

    def unsqueeze(self, d):
        return T(np.expand_dims(self.a, d))

    def clone(self):
        return T(self.a.copy())

    def cpu(self):
        return self

    def contiguous(self):
        return T(np.ascontiguousarray(self.a))


class Nested:
    def __init__(self, parts):
        self.parts = parts

    def unbind(self):
        return list(self.parts)


def main():
    # fake modules the package imports
    mm = make_mm()
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["torch"] = make_torch()

    cu = types.ModuleType("comfy.utils")
    cu.common_upscale = lambda s, w, h, m, c: T(
        np.zeros((s.shape[0], 3, h, w), dtype=np.float32))
    sys.modules["comfy.utils"] = cu
    sys.modules["comfy"].utils = cu

    mb = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **kw):
            return {}
    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb

    captured = {}
    nh = types.ModuleType("node_helpers")

    def conditioning_set_values(cond, values, append=False):
        out = []
        for item in cond:
            meta = item[1].copy()
            for key, incoming in values.items():
                value = incoming
                if append and meta.get(key) is not None:
                    value = meta[key] + incoming
                meta[key] = value
            out.append([item[0], meta])
        captured.clear()
        if out:
            captured.update(out[0][1])
        return out
    nh.conditioning_set_values = conditioning_set_values
    sys.modules["node_helpers"] = nh

    import os
    import tempfile
    outdir = tempfile.mkdtemp()
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: outdir

    def get_save_image_path(prefix, out, *a):
        sub, name = os.path.split(prefix)
        folder = os.path.join(out, sub)
        os.makedirs(folder, exist_ok=True)
        counter = 1 + sum(1 for f in os.listdir(folder)
                          if f.startswith(name))
        return folder, name, counter, sub, prefix
    fp.get_save_image_path = get_save_image_path
    sys.modules["folder_paths"] = fp

    st = types.ModuleType("safetensors")
    stt = types.ModuleType("safetensors.torch")

    def save_file(d, path, metadata=None):
        np.savez(path + ".npz", **{k: v.a for k, v in d.items()})
        open(path, "w").write(path + ".npz")

    def load_file(path):
        real = open(path).read()
        z = np.load(real)
        return {k: T(z[k]) for k in z.files}
    stt.save_file, stt.load_file = save_file, load_file
    st.torch = stt
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = stt

    # import the package by file location so it works whatever the repo
    # folder is called (ComfyUI-H3-Motion-Context, h3_motion_context, ...)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "h3mc_pkg", os.path.join(_PKG_DIR, "__init__.py"),
        submodule_search_locations=[_PKG_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["h3mc_pkg"] = pkg
    spec.loader.exec_module(pkg)  # applies patches, registers nodes
    nodes = sys.modules["h3mc_pkg.nodes"]
    assert pkg.NODE_CLASS_MAPPINGS

    # a 124-frame clip: latent_t 37 (7 full 17-frame groups + 1 + 4),
    # audio grid ceil(124 * 5/3) = 207 steps, overhang exactly 1/3
    latent_t, frames, audio_t = 37, 124, 207
    assert nodes._pixel_frames(latent_t) == frames
    h, w = 480 // 16, 864 // 16
    target = {"samples": Nested([
        T(np.zeros((1, 16, latent_t, h, w), dtype=np.float32)),
        T(np.zeros((1, 32, 2, audio_t), dtype=np.float32)),
    ])}
    # previous clip's sampler latent (same dims in this setup)
    prev = {"samples": Nested([
        T(np.zeros((1, 16, latent_t, h, w), dtype=np.float32)),
        T(np.arange(1 * 32 * 2 * audio_t, dtype=np.float32
                    ).reshape(1, 32, 2, audio_t)),
    ])}
    context = T(np.zeros((124, 480, 864, 3), dtype=np.float32))

    class VAE:
        def encode(self, x):
            n = x.shape[0]
            steps = max(1, (n - 5) // 17 * 5 + 2)
            return T(np.zeros((1, 16, steps, h, w), dtype=np.float32))

    node = nodes.H3Context()
    # Simulate conditioning produced by MiniMaxH3ReferenceToVideo. Motion
    # Context must append its timeline-audio block without dropping either
    # existing Ref2VA block.
    r2v_refs = [
        {"kind": "image", "latent_h": h, "latent_w": w,
         "latent": T(np.zeros((1, 16, 1, h, w), dtype=np.float32))},
        {"kind": "video", "latent_t": 2, "latent_h": h, "latent_w": w,
         "ref_audio_t": 0,
         "latent": T(np.zeros((1, 16, 2, h, w), dtype=np.float32))},
        {"kind": "audio", "ref_audio_t": 3,
         "audio_latent": T(np.zeros((1, 32, 2, 3), dtype=np.float32))},
    ]
    r2v_conditioning = [["c", {"minimax_refs": r2v_refs}]]
    out, trim = node.apply(
        conditioning=r2v_conditioning, vae=VAE(), latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", context_latent=prev)

    kfs = captured["minimax_keyframes"]
    assert len(kfs) == 7, len(kfs)
    idx = [kf[nodes.MC_KEY] for kf in kfs]
    assert idx == [0, 1, 5, 9, 13, 17, 18], idx
    assert captured["minimax_frame_count"] == frames
    assert trim == 22

    refs = captured["minimax_refs"]
    assert refs[:3] == r2v_refs
    assert len(refs) == 4
    assert out[0][1]["minimax_refs"] == refs
    assert r2v_conditioning[0][1]["minimax_refs"] == r2v_refs  # no mutation
    ref = refs[-1]
    assert ref["kind"] == "audio"
    assert ref["ref_audio_t"] == 37, ref["ref_audio_t"]  # round(22/24*40)
    tail = ref["audio_latent"]
    assert tuple(tail.shape) == (1, 32, 2, 37), tail.shape
    # tail must be the LAST 37 steps of the source
    assert float(tail.a[0, 0, 0, -1]) == float(prev["samples"].parts[1]
                                               .a[0, 0, 0, -1])
    overhang = audio_t - nodes.FRAME_RESCALE * frames  # 207 - 206.667
    want_end = 22 + overhang / nodes.FRAME_RESCALE
    got_end = ref[nodes.MC_AUDIO_KEY]
    assert abs(got_end - want_end) < 1e-9, (got_end, want_end)
    assert abs(got_end - 22.2) < 1e-6, got_end
    print("Ref2VA latent path: image/video/audio refs preserved + MC audio; "
          "7 cond blocks at %s, audio 37 steps sliced from latent tail, "
          "end_frame %.4f (overhang-compensated)" % (idx, got_end))

    # decoded-audio path must still work and carry integer end_frame
    captured.clear()

    class AudioVAE:
        audio_sample_rate = 32000

        def encode(self, x):
            steps = int(round(x.shape[-2] / 32000 * 40))
            return T(np.zeros((1, 32, 2, steps), dtype=np.float32))

    audio = {"waveform": T(np.zeros((1, 2, 32000), dtype=np.float32)),
             "sample_rate": 32000}
    node.apply(
        conditioning=[["c", {}]], vae=VAE(), latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", audio_vae=AudioVAE(), context_audio=audio)
    ref2 = captured["minimax_refs"][0]
    assert abs(ref2[nodes.MC_AUDIO_KEY] - 22.0) < 1e-9
    print("vae path: unchanged, end_frame %.1f" % ref2[nodes.MC_AUDIO_KEY])

    # video_source="latent": pinned video sliced straight from the previous
    # clip's latent -- phase-aligned tail, no VAE call, content identity
    captured.clear()

    class ForbiddenVAE:
        def encode(self, x):
            raise AssertionError("latent path must not touch the VAE")

    prev_marked = {"samples": Nested([
        T(np.arange(1 * 16 * latent_t * h * w, dtype=np.float32
                    ).reshape(1, 16, latent_t, h, w)),
        prev["samples"].parts[1],
    ])}
    out3, trim3 = node.apply(
        conditioning=[["c", {}]], vae=ForbiddenVAE(), latent=target,
        context_length=22, encode_mode="video", anchor_mode="head",
        crop="disabled", audio_context_length=22, audio_mode="timeline",
        video_source="latent", context_latent=prev_marked)
    kfs3 = captured["minimax_keyframes"]
    # latent_t=37, 37%5==2: phase-0 tail runs are 5 (2 steps) and 22 (7
    # steps). context_length 22 -> slice steps 30..36
    assert len(kfs3) == 7, len(kfs3)
    assert [kf[nodes.MC_KEY] for kf in kfs3] == [0, 1, 5, 9, 13, 17, 18]
    assert trim3 == 22, trim3
    src = prev_marked["samples"].parts[0]
    for j, kf in enumerate(kfs3):
        want = src.a[:, :, 30 + j:31 + j]
        got = kf["latent"].a
        assert got.shape == want.shape and np.array_equal(got, want), j
    print("latent video path: 7 blocks are steps 30..36 of the source "
          "latent verbatim, offsets phase-aligned, VAE untouched, trim 22")

    # the latent path must work with NO vae wired at all
    captured.clear()
    out4, trim4 = node.apply(
        conditioning=[["c", {}]], latent=target,
        context_length=22, encode_mode="video", anchor_mode="head",
        crop="disabled", audio_context_length=22, audio_mode="timeline",
        video_source="latent", context_latent=prev_marked)
    assert len(captured["minimax_keyframes"]) == 7 and trim4 == 22
    try:
        node.apply(conditioning=[["c", {}]], latent=target,
                   context_length=22, encode_mode="video",
                   anchor_mode="head", crop="disabled",
                   audio_context_length=22, audio_mode="timeline",
                   video_source="frames", context_frames=context)
        raise AssertionError("frames path ran without a vae")
    except ValueError as exc:
        assert "needs the video vae" in str(exc), str(exc)
    print("vae optional: latent path runs unwired, frames path refuses "
          "with a clear message")

    # 56-frame window (upstream 0.2.0 parity): a 141-frame clip is 42
    # latent steps; the 17-step phase-0 tail starts at step 25 and covers
    # exactly 56 pixel frames
    lt56 = 42
    assert nodes._pixel_frames(lt56) == 141
    at56 = 235                          # 141 * 5/3 exactly, overhang 0
    prev56 = {"samples": Nested([
        T(np.arange(1 * 16 * lt56 * h * w, dtype=np.float32
                    ).reshape(1, 16, lt56, h, w)),
        T(np.zeros((1, 32, 2, at56), dtype=np.float32)),
    ])}
    tgt56 = {"samples": Nested([
        T(np.zeros((1, 16, lt56, h, w), dtype=np.float32)),
        T(np.zeros((1, 32, 2, at56), dtype=np.float32)),
    ])}
    captured.clear()
    out56, trim56 = node.apply(
        conditioning=[["c", {}]], latent=tgt56,
        context_length=56, encode_mode="video", anchor_mode="head",
        crop="disabled", audio_context_length=56, audio_mode="timeline",
        video_source="latent", context_latent=prev56)
    kf56 = captured["minimax_keyframes"]
    assert len(kf56) == 17, "expected 17 pinned steps, got %d" % len(kf56)
    assert trim56 == 56, trim56
    src56 = prev56["samples"].parts[0]
    for j, kf in enumerate(kf56):
        want = src56.a[:, :, 25 + j:26 + j]
        got = kf["latent"].a
        assert got.shape == want.shape and np.array_equal(got, want), j
    print("56-frame window: 17 blocks are steps 25..41 of the source "
          "verbatim, trim 56")

    # guards: unwired latent, resolution mismatch, no usable run
    captured.clear()
    for kwargs, expect in [
        (dict(video_source="latent"), "context_latent is not wired"),
        (dict(video_source="latent",
              context_latent={"samples": Nested([
                  T(np.zeros((1, 16, latent_t, h, w + 1), np.float32)),
                  prev["samples"].parts[1]])}),
         "cannot resize"),
        (dict(video_source="latent", context_length=1,
              context_latent=prev_marked), "no phase-aligned tail run"),
        (dict(video_source="frames"), "context_frames is not wired"),
    ]:
        try:
            node.apply(conditioning=[["c", {}]], vae=ForbiddenVAE(),
                       latent=target, context_length=kwargs.pop(
                           "context_length", 22),
                       encode_mode="video", anchor_mode="head",
                       crop="disabled", audio_context_length=22,
                       audio_mode="timeline", **kwargs)
            raise AssertionError("expected failure: %s" % expect)
        except ValueError as exc:
            assert expect in str(exc), (expect, str(exc))
    print("latent video guards: unwired latent, resolution mismatch, no "
          "usable run, unwired frames all rejected loudly")

    # save -> load -> context_latent roundtrip across "runs"
    import time
    saver = nodes.H3ContextSaveLatent()
    loader = nodes.H3ContextLoadLatent()
    (p1,) = saver.save(prev, "h3_context/clip")
    time.sleep(0.02)
    prev2 = {"samples": Nested([
        prev["samples"].parts[0],
        T(prev["samples"].parts[1].a * 2.0),  # distinguishable content
    ])}
    (p2,) = saver.save(prev2, "h3_context/clip")
    assert p1 != p2
    (loaded,) = loader.load("h3_context")  # folder -> newest = p2
    parts = loaded["samples"]
    assert isinstance(parts, list) and len(parts) == 2
    captured.clear()
    node.apply(
        conditioning=[["c", {}]], vae=VAE(), latent=target,
        context_frames=context, context_length=22, encode_mode="video",
        anchor_mode="head", crop="disabled", audio_context_length=22,
        audio_mode="timeline", context_latent=loaded)
    ref3 = captured["minimax_refs"][0]
    want = float(prev2["samples"].parts[1].a[0, 0, 0, -1])
    got = float(ref3["audio_latent"].a[0, 0, 0, -1])
    assert got == want, (got, want)  # newest save's content came through
    assert abs(ref3[nodes.MC_AUDIO_KEY] - 22.2) < 1e-6
    ic1 = loader.IS_CHANGED("h3_context")
    assert isinstance(ic1, str) and p2 in ic1  # cache keys on the real file
    print("save/load roundtrip: newest of 2 saves loaded, pinned, "
          "end_frame %.4f, cache key tracks the file" %
          ref3[nodes.MC_AUDIO_KEY])

    # retry safety with indexed slots: generating clip 3, re-rolling it
    # must overwrite slot 3 and always load slot 2, never its own save
    prevA = {"samples": Nested([prev["samples"].parts[0],
                                T(np.full((1, 32, 2, audio_t), 7.0,
                                          dtype=np.float32))])}
    prevB1 = {"samples": Nested([prev["samples"].parts[0],
                                 T(np.full((1, 32, 2, audio_t), 8.0,
                                           dtype=np.float32))])}
    prevB2 = {"samples": Nested([prev["samples"].parts[0],
                                 T(np.full((1, 32, 2, audio_t), 9.0,
                                           dtype=np.float32))])}
    (pa,) = saver.save(prevA, "h3_context/clip", clip_index=2)   # clip 2 ok
    assert pa.endswith("_00002.safetensors"), pa  # natural slot name
    time.sleep(0.02)
    (pb1,) = saver.save(prevB1, "h3_context/clip", clip_index=3)  # clip 3 try 1
    time.sleep(0.02)
    (pb2,) = saver.save(prevB2, "h3_context/clip", clip_index=3)  # re-roll
    assert pb1 == pb2 and pa != pb1  # re-roll overwrote its own slot
    # generating clip 3, continuing FROM clip 2: loader index is 2, literally
    (l3,) = loader.load("h3_context", clip_index=2)
    got = float(l3["samples"][1].a[0, 0, 0, 0])
    assert got == 7.0, got  # clip 2's latent, NOT the rejected attempt (8/9)
    # newest-file mode would have returned the reject: prove the hazard
    (lnew,) = loader.load("h3_context", clip_index=0)
    assert float(lnew["samples"][1].a[0, 0, 0, 0]) == 9.0
    # asking for a slot that was never saved says so plainly
    try:
        loader.load("h3_context", clip_index=7)
    except FileNotFoundError as e:
        assert "no saved latent for clip 7" in str(e)
    else:
        raise AssertionError("missing slot did not refuse")
    # an auto-numbered near-miss (trailing underscore) is never matched,
    # and the error explains the rename
    (pauto,) = saver.save(prevA, "h3_context/clip", clip_index=0)
    assert pauto.endswith("_.safetensors"), pauto
    import re as _re
    runno = int(_re.search(r"_(\d{5})_\.safetensors$", pauto).group(1))
    try:
        loader.load("h3_context", clip_index=runno)
    except FileNotFoundError as e:
        assert "trailing underscore" in str(e) and "rename" in str(e), str(e)
    else:
        raise AssertionError("auto-numbered file was matched by index")
    print("indexed slots: re-roll overwrites its slot, loads previous "
          "clip's latent; auto mode confirmed to return the reject")

    print("smoke test passed")


if __name__ == "__main__":
    main()
