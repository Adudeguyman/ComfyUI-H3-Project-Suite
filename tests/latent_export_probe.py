"""The latent export must deliver exactly what the MP4s deliver.

The saved latent is the FULL render; the MP4 beside it is trimmed. The
exporter therefore keeps the last meta["frames"] frames of each decode
and the matching tail of audio. Get that arithmetic wrong by one step
and every join in the master carries duplicated or missing frames - the
exact defect the whole chain exists to avoid.

Fake VAEs make the decode deterministic so delivery, trims, level
matching and the missing-latent refusal are all checkable without a GPU.
"""

import json
import os
import sys
import tempfile
import types

import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

_OUT = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: _OUT
sys.modules["folder_paths"] = fp


class T:
    def __init__(self, a):
        self.a = np.asarray(a, dtype=np.float32)
        self.shape = self.a.shape

    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.a


def st_load_stub(path):
    raw = json.load(open(path + ".json.stub"))
    return {k: T(np.array(v, dtype=np.float32)) for k, v in raw.items()}


st = types.ModuleType("safetensors")
stt = types.ModuleType("safetensors.torch")
stt.load_file = st_load_stub
stt.save_file = lambda *a, **k: None
st.torch = stt
sys.modules["safetensors"] = st
sys.modules["safetensors.torch"] = stt

import importlib.util  # noqa: E402

pkg = types.ModuleType("h3le")
pkg.__path__ = [_PKG]
sys.modules["h3le"] = pkg
for sub in ("level_match", "export_latents"):
    spec = importlib.util.spec_from_file_location(
        "h3le." + sub, os.path.join(_PKG, sub + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["h3le." + sub] = m
    spec.loader.exec_module(m)
ex = sys.modules["h3le.export_latents"]


class FakeVideoVAE:
    """Decodes a 'latent' [T] of per-frame lumas into [1,T,4,4,3]."""

    def decode(self, lat):
        v = lat.a.reshape(-1)
        frames = np.stack([np.full((4, 4, 3), x, dtype=np.float32)
                           for x in v])
        return T(frames[None])


class FakeAudioVAE:
    audio_sample_rate = 8

    def decode(self, lat):
        return T(lat.a.reshape(1, 1, -1))


class FakeProject:
    def __init__(self, root):
        self.clips_dir = root


def put_clip(root, basename, full_lumas, deliver, sr=8):
    stub = {"video": [float(x) for x in full_lumas],
            "audio": [float(i) for i in range(len(full_lumas) * 2)]}
    open(os.path.join(root, basename + ".safetensors.json.stub"),
         "w").write(json.dumps(stub))
    open(os.path.join(root, basename + ".safetensors"), "w").write("x")
    meta = {"frames": deliver, "fps": 8, "sample_rate": sr}
    workflow = {"nodes": [
        {"type": "VAELoader", "widgets_values": ["h3_video_vae.safetensors"]},
        {"type": "VAELoader", "widgets_values": ["h3_audio_vae.safetensors"]},
    ]}
    open(os.path.join(root, basename + ".json"), "w").write(
        json.dumps({"meta": meta, "workflow": workflow}))


def decode_master(path):
    import av
    lum = []
    with av.open(path) as c:
        for f in c.decode(video=0):
            lum.append(float(np.mean(f.to_ndarray(format="rgb24"))))
    return lum


def main():
    root = tempfile.mkdtemp()
    p = FakeProject(root)
    ex.register_vaes(FakeVideoVAE(), FakeAudioVAE())

    # clip 1: 6 rendered, 6 delivered (head of chain, no trim)
    put_clip(root, "clip_001_take1", [0.30] * 6, 6)
    # clip 2: 8 rendered, 5 delivered -> the exporter must keep the LAST 5
    put_clip(root, "clip_002_take1", [0.90] * 3 + [0.32] * 5, 5)

    clips = [{"index": 1, "basename": "clip_001_take1"},
             {"index": 2, "basename": "clip_002_take1"}]
    master = os.path.join(root, "master.mp4")
    info = ex.export_from_latents(p, clips, master, level_match=False)
    lum = decode_master(master)
    assert len(lum) == 11, "expected 6+5 frames, got %d" % len(lum)
    # the trimmed head (0.90 lumas -> ~230) must not appear anywhere
    assert max(lum) < 120, "a trimmed re-tread frame leaked: %s" % max(lum)
    print("1. delivery: 6+5 frames, the 3-frame re-tread head is gone")

    # level matching: give clip 2 a brighter head that decays
    put_clip(root, "clip_003_take1",
             [0.90] * 2 + [0.55, 0.47, 0.41, 0.37, 0.34, 0.33, 0.325,
                           0.32, 0.32, 0.32], 10)
    clips.append({"index": 3, "basename": "clip_003_take1"})
    info = ex.export_from_latents(p, clips, os.path.join(root, "m2.mp4"),
                                  level_match=True)
    assert 3 in info["level_matched"], info
    print("2. level matching engaged on the bright join, in float")

    # a missing latent refuses BEFORE writing anything
    clips.append({"index": 4, "basename": "clip_004_take1"})
    try:
        ex.export_from_latents(p, clips, os.path.join(root, "m3.mp4"))
    except RuntimeError as exc:
        assert "clip_004_take1" in str(exc)
        assert not os.path.exists(os.path.join(root, "m3.mp4")), \
            "refusal must come before the first written frame"
        print("3. missing latent: refused by name, nothing written")
    else:
        raise AssertionError("missing latent must refuse")

    # nothing registered: the export loads the VAEs the take was
    # rendered with, rather than demanding a generation first
    ex._VAES.clear()
    loaded = {}

    class FakeSD:
        @staticmethod
        def VAE(sd=None):
            v = FakeVideoVAE() if sd == "video" else FakeAudioVAE()
            v.latent_channels, v.latent_dim = (
                (24, 3) if sd == "video" else (32, 2))
            loaded[sd] = True
            return v

    fake_fp = types.ModuleType("folder_paths")
    fake_fp.get_output_directory = lambda: _OUT
    fake_fp.get_full_path = lambda kind, name: (
        "/fake/" + name if "vae" in name else None)
    sys.modules["folder_paths"] = fake_fp
    cs = types.ModuleType("comfy.sd")
    cs.VAE = FakeSD.VAE
    cu = types.ModuleType("comfy.utils")
    cu.load_torch_file = lambda path: (
        "video" if "video" in path else "audio")
    comfy = types.ModuleType("comfy")
    comfy.sd, comfy.utils = cs, cu
    sys.modules["comfy"] = comfy
    sys.modules["comfy.sd"] = cs
    sys.modules["comfy.utils"] = cu

    out5 = os.path.join(root, "m5.mp4")
    ex.export_from_latents(p, clips[:2], out5, level_match=False)
    assert os.path.exists(out5), "export with no registered VAEs failed"
    assert loaded.get("video") and loaded.get("audio"), loaded
    print("4. nothing registered: loaded both VAEs from the take's "
          "own workflow")

    # classification is by the loaded object, never the filename
    v = FakeVideoVAE(); v.latent_channels, v.latent_dim = 24, 3
    a = FakeAudioVAE(); a.latent_channels, a.latent_dim = 32, 2
    assert ex._classify(v) == "video" and ex._classify(a) == "audio"
    junk = FakeVideoVAE(); junk.latent_channels, junk.latent_dim = 16, 3
    assert ex._classify(junk) is None, "an unrelated VAE must not pass"
    print("5. VAEs classified by shape: 24/3 video, 32/2 audio, "
          "others refused")

    # and a chain whose sidecars name no VAE says what to do
    ex._VAES.clear()
    open(os.path.join(root, "clip_001_take1.json"), "w").write(
        json.dumps({"meta": {"frames": 6, "fps": 8}}))
    try:
        ex.export_from_latents(p, clips[:1], os.path.join(root, "m6.mp4"))
    except RuntimeError as exc:
        assert "no VAE loader" in str(exc), exc
        print("6. sidecar names no VAE: told to wire them and queue once")
    else:
        raise AssertionError("must refuse when no VAE can be found")

    print("all checks passed")


if __name__ == "__main__":
    main()
