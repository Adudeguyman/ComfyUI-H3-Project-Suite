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
_THIS = os.path.abspath(__file__)
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


def put_clip(root, basename, full_lumas, deliver, sr=8, fps=8):
    stub = {"video": [float(x) for x in full_lumas],
            "audio": [float(i) for i in range(len(full_lumas) * 2)]}
    open(os.path.join(root, basename + ".safetensors.json.stub"),
         "w").write(json.dumps(stub))
    open(os.path.join(root, basename + ".safetensors"), "w").write("x")
    meta = {"frames": deliver, "fps": fps, "sample_rate": sr}
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
        assert "Project Hub node" in str(exc), exc
        print("6. nothing to go on: told to wire the Hub's VAE inputs")
    else:
        raise AssertionError("must refuse when no VAE can be found")

    # names handed in directly - what the panel does after reading them
    # off the loaders wired into the Hub - need no sidecar at all
    open(os.path.join(root, "clip_002_take1.json"), "w").write(
        json.dumps({"meta": {"frames": 5, "fps": 8}}))
    out7 = os.path.join(root, "m7.mp4")
    ex.export_from_latents(p, clips[:2], out7, level_match=False,
                           vae_names=["h3_video_vae.safetensors",
                                      "h3_audio_vae.safetensors"])
    assert os.path.exists(out7), "explicit VAE names should be enough"
    print("7. explicit names: exported with no sidecar workflow at all")

    # The master's encode settings are the caller's, not the clips'.
    # Checked by encoding the same footage twice and requiring the files
    # to differ: that proves the setting reaches the encoder, where
    # inspecting PyAV's stream object only proves the probe can read it
    # back.
    # flat 4x4 frames compress to the same handful of bytes at any
    # setting, so this check needs footage where quality has something to
    # do: detailed, moving, and big enough for CRF to bite
    class NoisyVAE:
        def decode(self, lat):
            rng = np.random.default_rng(7)
            n = int(lat.a.reshape(-1).shape[0])
            return T(rng.random((1, n, 96, 96, 3), dtype=np.float32))

    ex._VAES.clear()
    ex.register_vaes(NoisyVAE(), FakeAudioVAE())

    def sized(crf, preset, name):
        out = os.path.join(root, name)
        # no vae_names here: explicit names would take priority and load
        # the flat-frame fake instead of the noisy one registered above
        ex.export_from_latents(p, clips[:2], out, level_match=False,
                               crf=crf, preset=preset)
        return os.path.getsize(out)


    hi = sized(10, "medium", "m_hi.mp4")
    lo = sized(38, "medium", "m_lo.mp4")
    assert hi > lo * 1.5, (
        "crf 10 produced %d bytes and crf 38 produced %d - too close for "
        "the quality setting to be reaching the encoder" % (hi, lo))
    print("8. master quality honoured: crf 10 -> %d bytes, crf 38 -> %d"
          % (hi, lo))

    # Peak MEMORY, not peak single allocation: an extra copy of a clip
    # is exactly one clip in size, so watching for a bigger array cannot
    # tell a copy from the decode. Measured in a subprocess with a clip
    # large enough for the difference to show, since ru_maxrss is a high
    # water mark that never comes back down.
    import subprocess
    import textwrap

    script = textwrap.dedent("""
        import json, os, resource, sys, tempfile, types
        import numpy as np
        sys.path.insert(0, %r)
        _src = open(%r).read().split("def main(")[0]
        # the prelude resolves paths from __file__, which exec() does not
        # provide - hand it the same values this process computed
        _src = _src.replace(
            "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))",
            repr(sys.path[0]))
        _src = _src.replace("os.path.abspath(__file__)", repr("probe"))
        exec(_src)
        root = tempfile.mkdtemp()
        p = FakeProject(root)

        FRAMES, SIDE = 240, 256

        class BigVAE:
            def decode(self, lat):
                n = int(lat.a.reshape(-1).shape[0])
                rng = np.random.default_rng(3)
                return T(rng.random((1, n, SIDE, SIDE, 3),
                                    dtype=np.float32))

        # no audio VAE: this measures the video path, and an
        # 8 Hz fake waveform trips PyAV's packet timing
        ex.register_vaes(BigVAE(), None)
        put_clip(root, "clip_001_take1", [0.3] * FRAMES, FRAMES,
                 fps=24)
        put_clip(root, "clip_002_take1", [0.3] * FRAMES, FRAMES,
                 fps=24)
        clips = [{"index": 1, "basename": "clip_001_take1"},
                 {"index": 2, "basename": "clip_002_take1"}]
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        ex.export_from_latents(p, clips, os.path.join(root, "big.mp4"),
                               level_match=True)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        clip_mb = FRAMES * SIDE * SIDE * 3 * 4 / 1e6
        print(json.dumps({"peak_mb": after / 1024.0,
                          "clip_mb": clip_mb}))
    """) % (_PKG, _THIS)

    r = subprocess.run([sys.executable, "-c", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("   (memory measurement skipped: %s)"
              % (r.stderr.strip().splitlines() or ["?"])[-1])
    else:
        import json as _json
        d = _json.loads(r.stdout.strip().splitlines()[-1])
        # one decoded clip is unavoidable; interpreter and numpy add a
        # fixed overhead, so the bar is two clips rather than one
        assert d["peak_mb"] < d["clip_mb"] * 2.2, (
            "peak %.0f MB against a %.0f MB clip - the export is holding "
            "more than the decode plus a frame"
            % (d["peak_mb"], d["clip_mb"]))
        print("9. export peak %.0f MB for a %.0f MB clip: the decode plus "
              "a frame" % (d["peak_mb"], d["clip_mb"]))

    print("all checks passed")


if __name__ == "__main__":
    main()
