"""The project loop, end to end, with no ComfyUI and no GPU.

Drives exactly the graph the Hub design promises:

  Hub(empty)  -> chain_active False, placeholder latent, next clip_001_take1
  H3 Context(enabled=False) -> passthrough, trim 0, no patches touched
  Project Save -> pair written atomically, manifest pending
  approve      -> Hub re-resolves: chain_active True, context IS clip 1's
                  saved latent, IS_CHANGED token moved
  H3 Context(enabled=True, video_source=latent) -> pins from that latent
  re-roll + reject exercised through Save/manifest

The mp4 encoder is stubbed (PyAV needs real codecs); everything else runs
the real code. What the stub means: naming, pairing, ordering and manifest
transitions are verified here -- actual h264/aac encoding is verified only
on a real install.
"""

import os
import sys
import types
import tempfile

import numpy as np

_TESTS = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_TESTS)
sys.path.insert(0, _TESTS)

from _mock_harness import make_mm  # noqa: E402
from _node_smoke_test import T, Nested  # noqa: E402


def main():
    out = tempfile.mkdtemp()

    # ---- stub ComfyUI's modules, smoke-test style ----
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: out
    fp.get_save_image_path = lambda p, d, *a: (d, os.path.basename(p), 1,
                                               "", "")
    sys.modules["folder_paths"] = fp

    cu = types.ModuleType("comfy.utils")
    cu.common_upscale = lambda img, w, h, m, c: img
    sys.modules.setdefault("comfy", types.ModuleType("comfy"))
    sys.modules["comfy"].utils = cu
    sys.modules["comfy.utils"] = cu

    nh = types.ModuleType("node_helpers")
    captured = {}

    def conditioning_set_values(cond, values, append=False):
        outc = []
        for item in cond:
            meta = dict(item[1])
            for k, v in values.items():
                if append and meta.get(k):
                    v = meta[k] + v
                meta[k] = v
            outc.append([item[0], meta])
        captured.clear()
        if outc:
            captured.update(outc[0][1])
        return outc
    nh.conditioning_set_values = conditioning_set_values
    sys.modules["node_helpers"] = nh

    st = types.ModuleType("safetensors")
    stt = types.ModuleType("safetensors.torch")

    def save_file(d, path, metadata=None):
        np.savez(path + ".npz", **{k: v.a for k, v in d.items()})
        open(path, "w").write(path + ".npz")

    def load_file(path):
        z = np.load(open(path).read())
        return {k: T(z[k]) for k in z.files}
    stt.save_file, stt.load_file = save_file, load_file
    st.torch = stt
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = stt

    tt = types.ModuleType("torch")
    tt.zeros = lambda shape: T(np.zeros(shape, dtype=np.float32))
    tt.equal = lambda a, b: np.array_equal(getattr(a, "a", a),
                                           getattr(b, "a", b))
    sys.modules["torch"] = tt

    sys.modules["comfy.ldm"] = types.ModuleType("comfy.ldm")
    sys.modules["comfy.ldm.minimax"] = types.ModuleType("comfy.ldm.minimax")
    mm = make_mm()
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]

    mb = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **kw):
            return {}
    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "h3p", os.path.join(_PKG, "__init__.py"),
        submodule_search_locations=[_PKG])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["h3p"] = pkg
    spec.loader.exec_module(pkg)
    nodes = sys.modules["h3p.nodes"]
    pn = sys.modules["h3p.project_nodes"]
    assert "H3ProjectHub" in pkg.NODE_CLASS_MAPPINGS
    assert "H3ProjectSave" in pkg.NODE_CLASS_MAPPINGS

    # stub the encoder: PyAV needs real codecs; record what would be written
    encoded = []

    def fake_write(path, images, audio, fps):
        encoded.append((path, int(images.shape[0]),
                        None if audio is None else int(
                            audio["waveform"].shape[-1]), fps))
        open(path, "w").write("mp4:%d" % images.shape[0])
    pn._write_video = fake_write

    hub = pn.H3ProjectHub()
    saver = pn.H3ProjectSave()
    ctx_node = nodes.H3Context()

    # ---- 1: empty project resolves inactive ----
    handle, context, active, status = hub.resolve("LoopTest", True)
    assert active is False
    assert "fresh clip 1" in status and "clip_001_take1" in status
    tok0 = pn.H3ProjectHub.IS_CHANGED("LoopTest")
    print("1. empty project: chain_active False -", status)

    # ---- 2: enabled=False is a true passthrough ----
    cond_in = [["c", {"minimax_refs": [{"kind": "image"}]}]]
    out_cond, trim = ctx_node.apply(
        conditioning=cond_in, vae=None, latent=None, context_length=22,
        encode_mode="video", anchor_mode="head", crop="disabled",
        audio_context_length=22, audio_mode="timeline",
        context_latent=context, enabled=active)
    assert out_cond is cond_in and trim == 0
    print("2. H3 Context enabled=False: conditioning identical, trim 0, "
          "placeholder latent never touched")

    # ---- 3: "render" clip 1 and save the pair ----
    latent_t, h, w, frames = 37, 30, 54, 124
    def fresh_latent(scale):
        return {"samples": Nested([
            T(np.full((1, 16, latent_t, h, w), scale, dtype=np.float32)),
            T(np.full((1, 32, 2, 207), scale, dtype=np.float32)),
        ])}
    images = T(np.zeros((frames - 22, 480, 864, 3), dtype=np.float32))
    audio = {"waveform": T(np.zeros((1, 2, 32000 * 4), dtype=np.float32)),
             "sample_rate": 32000}
    (base1,) = saver.save(handle, fresh_latent(1.0), images, 24, audio)
    assert base1 == "clip_001_take1"
    cdir = os.path.join(out, "h3_projects", "LoopTest", "clips")
    assert os.path.isfile(os.path.join(cdir, "clip_001_take1.mp4"))
    assert os.path.isfile(os.path.join(cdir, "clip_001_take1.safetensors"))
    assert encoded[-1][1] == frames - 22
    print("3. Project Save wrote the pair as clip_001_take1, "
          "manifest pending")

    # still inactive: pending does not arm the chain, but token moved
    handle, context, active, status = hub.resolve("LoopTest", True)
    assert active is False and "PENDING" in status
    assert pn.H3ProjectHub.IS_CHANGED("LoopTest") != tok0
    print("4. pending clip does not arm the chain; IS_CHANGED moved")

    # ---- 5: approve via manifest (what the route does), re-resolve ----
    from h3p.project import Project
    Project(out, "LoopTest").approve()
    handle, context, active, status = hub.resolve("LoopTest", True)
    assert active is True
    src = context["samples"]
    parts = src.parts if hasattr(src, "parts") else src
    assert float(parts[0].a[0, 0, 0, 0, 0]) == 1.0  # clip 1's own latent
    assert "clip_002_take1" in status
    print("5. approve armed the chain; context IS clip 1's saved latent")

    # ---- 6: chain node pins video+audio from that context ----
    captured.clear()
    out_cond, trim = ctx_node.apply(
        conditioning=[["c", {}]], vae=None, latent=fresh_latent(0.0),
        context_length=22, encode_mode="video", anchor_mode="head",
        crop="disabled", audio_context_length=22, audio_mode="timeline",
        video_source="latent", context_latent=context, enabled=active)
    kfs = captured["minimax_keyframes"]
    assert len(kfs) == 7 and trim == 22
    assert float(kfs[0]["latent"].a[0, 0, 0, 0, 0]) == 1.0
    print("6. extension pinned 7 video blocks + audio from the project "
          "latent, trim 22")

    # ---- 7: re-roll then reject through the same machinery ----
    (base2,) = saver.save(handle, fresh_latent(2.0), images, 24, audio)
    assert base2 == "clip_002_take1"
    (base3,) = saver.save(handle, fresh_latent(3.0), images, 24, audio)
    assert base3 == "clip_002_take2"  # re-roll: same index, next take
    trash = os.path.join(out, "h3_projects", "LoopTest", ".trash")
    assert os.path.isfile(os.path.join(trash, "clip_002_take1.mp4"))
    Project(out, "LoopTest").reject()
    handle, context, active, status = hub.resolve("LoopTest", True)
    assert active is True and "clip_002_take1" in status
    assert float((context["samples"].parts if hasattr(
        context["samples"], "parts") else context["samples"])[0]
        .a[0, 0, 0, 0, 0]) == 1.0  # still chains from clip 1
    print("7. re-roll superseded to .trash/, reject dropped clip 2; chain "
          "still tails clip 1, next render clip_002_take1 again")

    print("all checks passed")


if __name__ == "__main__":
    main()
