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
    open(os.path.join(root, basename + ".json"), "w").write(
        json.dumps({"meta": meta}))


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

    # no VAEs registered: plain message, not a traceback
    ex._VAES.clear()
    try:
        ex.export_from_latents(p, clips[:1],
                               os.path.join(root, "m4.mp4"))
    except RuntimeError as exc:
        assert "no VAEs registered" in str(exc)
        print("4. no VAEs this session: refused with the reason")
    else:
        raise AssertionError("must refuse without VAEs")

    print("all checks passed")


if __name__ == "__main__":
    main()
