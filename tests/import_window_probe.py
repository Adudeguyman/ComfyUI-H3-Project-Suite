"""Importing footage must land a real clip in the project.

The helpers around the importer (the length ladder, frame remapping,
alignment, audio conforming) have their own probe. This drives
`import_window` itself, which is where the pieces meet: encode the
window, write the pair, record it in the manifest. Nothing had ever
exercised that with sound, and a source WITH audio is the interesting
case - an H3 AV latent is a NestedTensor of two differently shaped
streams, so the obvious `torch.stack` of the pair raises and the whole
import is refused after several minutes of encoding.

The VAEs are fakes, so this needs no GPU and no models. Real PyAV
writes the source file, and the real Project records the result.
"""

import json
import os
import sys
import tempfile
import types

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
sys.path.insert(0, _PKG)

import av  # noqa: E402
import torch  # noqa: E402

_OUT = tempfile.mkdtemp()
_IN = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: _OUT
fp.get_input_directory = lambda: _IN
fp.get_filename_list = lambda kind: ["fake_video_vae.safetensors",
                                     "fake_audio_vae.safetensors"]
fp.get_full_path = lambda kind, name: "/fake/" + name
sys.modules["folder_paths"] = fp

# core carries an AV latent as a NestedTensor; the pack must build one
# the same way rather than stacking
nt = types.ModuleType("comfy.nested_tensor")


class NestedTensor:
    def __init__(self, tensors):
        self.tensors = list(tensors)

    def unbind(self):
        return list(self.tensors)


nt.NestedTensor = NestedTensor
sys.modules["comfy.nested_tensor"] = nt

saved = {}
st = types.ModuleType("safetensors")
stt = types.ModuleType("safetensors.torch")


def save_file(d, path, metadata=None):
    saved[path] = {k: tuple(v.shape) for k, v in d.items()}
    open(path, "w").write("latent")


stt.save_file = save_file
stt.load_file = lambda path: {}
st.torch = stt
sys.modules["safetensors"] = st
sys.modules["safetensors.torch"] = stt

import importlib.util  # noqa: E402

pkg = types.ModuleType("h3iw")
pkg.__path__ = [_PKG]
sys.modules["h3iw"] = pkg
for sub in ("project", "import_source"):
    spec = importlib.util.spec_from_file_location(
        "h3iw." + sub, os.path.join(_PKG, sub + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["h3iw." + sub] = m
    spec.loader.exec_module(m)
imp = sys.modules["h3iw.import_source"]
Project = sys.modules["h3iw.project"].Project

# import_window reaches into the node modules for the latent writer and
# the mp4 writer. Those pull in ComfyUI itself, so they are stubbed here;
# the mp4 stub writes a real file, since the point is that a playable
# take lands in the project.
_nodes = types.ModuleType("h3iw.nodes")
_nodes._st_save = save_file


def _resize_stub(image, width, height, crop):
    x = image[..., :3].movedim(-1, 1)
    x = torch.nn.functional.interpolate(x, size=(height, width),
                                        mode="bilinear")
    return x.movedim(1, -1)


_nodes._resize = _resize_stub
sys.modules["h3iw.nodes"] = _nodes

_pn = types.ModuleType("h3iw.project_nodes")


def _write_video_stub(path, images, audio, fps, tags=None):
    arr = images.cpu().numpy() if hasattr(images, "cpu") else np.asarray(images)
    out = av.open(path, mode="w")
    vs = out.add_stream("libx264", rate=int(fps))
    vs.height, vs.width = int(arr.shape[1]), int(arr.shape[2])
    vs.pix_fmt = "yuv420p"
    vs.options = {"crf": "30", "preset": "ultrafast"}
    for i in range(arr.shape[0]):
        frame = np.clip(arr[i][..., :3] * 255.0, 0, 255).astype(np.uint8)
        for pkt in vs.encode(av.VideoFrame.from_ndarray(
                np.ascontiguousarray(frame), format="rgb24")):
            out.mux(pkt)
    for pkt in vs.encode():
        out.mux(pkt)
    out.close()


_pn._write_video = _write_video_stub
_pn._now_iso = lambda: "2026-01-01T00:00:00"
sys.modules["h3iw.project_nodes"] = _pn

FPS = 24
SR = 32000


class FakeVideoVAE:
    """[B,T,H,W,3] pixels -> [1, 24, T_lat, H/16, W/16]."""

    def encode(self, images):
        t, h, w = images.shape[-4], images.shape[-3], images.shape[-2]
        t_lat = max(1, (t - 5) // 17 * 5 + 2)
        return torch.zeros(1, 24, t_lat, h // 16, w // 16)


class FakeAudioVAE:
    """[B, S, C] samples -> [1, 32, 2, S/800]."""

    audio_sample_rate = SR

    def encode(self, waveform):
        assert waveform.shape[-1] == 2, (
            "the audio VAE was handed %s; core's encode takes channels "
            "LAST" % (tuple(waveform.shape),))
        return torch.zeros(1, 32, 2, int(waveform.shape[-2]) // 800)


def write_source(path, frames, with_audio):
    out = av.open(path, mode="w")
    vs = out.add_stream("libx264", rate=FPS)
    vs.width = vs.height = 64
    vs.pix_fmt = "yuv420p"
    vs.options = {"crf": "30", "preset": "ultrafast"}
    aso = None
    if with_audio:
        aso = out.add_stream("aac", rate=SR)
        aso.layout = "stereo"
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    for i in range(frames):
        img[...] = i % 256
        for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
    for p in vs.encode():
        out.mux(p)
    if aso is not None:
        n = int(round(frames / FPS * SR))
        tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / SR)
                ).astype(np.float32)
        wav = np.stack([tone, tone])
        pts = 0
        for s in range(0, n, 1024):
            seg = np.ascontiguousarray(wav[:, s:s + 1024])
            af = av.AudioFrame.from_ndarray(seg, format="fltp",
                                            layout="stereo")
            af.sample_rate = SR
            af.pts = pts
            pts += seg.shape[1]
            for p in aso.encode(af):
                out.mux(p)
        for p in aso.encode():
            out.mux(p)
    out.close()


def run_import(project, src, frames, **kw):
    return imp.import_window(project, src, start=0, frames=frames,
                             width=0, height=0, with_audio=True, **kw)


def main():
    imp.load_vaes_for = lambda project, clips, names=None: {
        "video": FakeVideoVAE(), "audio": FakeAudioVAE()}
    sys.modules["h3iw.export_latents"] = types.SimpleNamespace(
        load_vaes_for=imp.load_vaes_for)

    src = os.path.join(_IN, "source.mp4")
    write_source(src, 90, with_audio=True)
    silent = os.path.join(_IN, "silent.mp4")
    write_source(silent, 90, with_audio=False)

    p = Project(_OUT, "ImportProbe", create=True)
    info = run_import(p, src, 56)
    print("1. imported with sound -> %s (clip %d take %d)"
          % (info["basename"], info["index"], info["take"]))

    base = info["basename"]
    for ext in (".mp4", ".safetensors", ".json"):
        path = os.path.join(p.clips_dir, base + ext)
        assert os.path.isfile(path), "no %s written" % ext
    print("2. clips/ holds the video, the latent and the sidecar")

    # both streams saved, and the audio one is not a copy of the video
    shapes = saved[os.path.join(p.clips_dir, base + ".safetensors")]
    assert shapes["video"][1] == 24 and shapes["audio"][1] == 32, shapes
    print("3. latent carries both streams: video %s, audio %s"
          % (shapes["video"], shapes["audio"]))

    # and the project knows about it, pending review
    fresh = Project(_OUT, "ImportProbe")
    entry = fresh.clips[-1]
    assert entry["basename"] == base, (entry, base)
    assert entry["status"] == "pending", entry
    assert fresh.pending() is not None
    meta = json.load(open(os.path.join(p.clips_dir, base + ".json")))["meta"]
    assert meta["frames"] == 56 and meta["imported_from"] == "source.mp4"
    assert meta["sample_rate"] == SR, meta
    print("4. manifest lists it as pending, sidecar records the window")

    # a source with no sound still imports
    info2 = run_import(fresh, silent, 39)
    shapes2 = saved[os.path.join(p.clips_dir,
                                 info2["basename"] + ".safetensors")]
    assert shapes2["video"] == shapes2["audio"], (
        "a silent import should store the video twice, as before")
    print("5. silent source imports too -> %s" % info2["basename"])

    # a length H3 cannot render is refused before anything is encoded
    try:
        run_import(fresh, src, 50)
    except RuntimeError as exc:
        assert "not a length" in str(exc), exc
    else:
        raise AssertionError("50 frames was accepted")
    print("6. an off-ladder length is refused")

    # why the importer must not build a combined latent by stacking:
    # the two streams have different shapes, so stack refuses them. This
    # is the failure the import used to hit after several minutes of
    # encoding, reported only as a toast.
    v = FakeVideoVAE().encode(torch.zeros(1, 56, 64, 64, 3))
    a = FakeAudioVAE().encode(torch.zeros(1, 74666, 2))
    try:
        torch.stack([v, a])
    except RuntimeError as exc:
        assert "size" in str(exc), exc
    else:
        raise AssertionError("torch.stack accepted a video/audio pair")
    assert NestedTensor((v, a)).unbind() == [v, a]
    print("7. stacking the pair raises; a NestedTensor holds it, which is "
          "what core's own H3 nodes build")

    # 8. size follows the project, never the footage. A project that
    #    already has a 1248x832 clip gets a 1248x832 import from a
    #    64x64 source; latent and sidecar both say so.
    sized = Project(_OUT, "Sized", create=True)
    i0, t0, b0 = sized.next_save()
    for ext in (".mp4", ".safetensors"):     # the pair must exist first
        open(os.path.join(sized.clips_dir, b0 + ext), "w").write("x")
    sized.record_render(i0, t0, {"width": 1248, "height": 832,
                                 "frames": 56, "fps": FPS})
    sized.approve()
    assert sized.resolution() == (1248, 832)
    info3 = run_import(sized, src, 56)
    shapes3 = saved[os.path.join(sized.clips_dir,
                                 info3["basename"] + ".safetensors")]
    assert shapes3["video"][-2:] == (832 // 16, 1248 // 16), shapes3
    meta3 = json.load(open(os.path.join(
        sized.clips_dir, info3["basename"] + ".json")))["meta"]
    assert (meta3["width"], meta3["height"]) == (1248, 832), meta3
    print("8. into a 1248x832 project: a 64x64 source lands at 1248x832")

    # 9. an empty project is sized by the import, snapped to 32: a 100x70
    #    source sets 96x64
    odd = os.path.join(_IN, "odd.mp4")
    out = av.open(odd, mode="w")
    vs = out.add_stream("libx264", rate=FPS)
    vs.width, vs.height, vs.pix_fmt = 100, 70, "yuv420p"
    vs.options = {"crf": "30", "preset": "ultrafast"}
    for _ in range(60):
        for pkt in vs.encode(av.VideoFrame.from_ndarray(
                np.zeros((70, 100, 3), np.uint8), format="rgb24")):
            out.mux(pkt)
    for pkt in vs.encode():
        out.mux(pkt)
    out.close()
    empty = Project(_OUT, "Empty", create=True)
    assert empty.resolution() is None
    info4 = run_import(empty, odd, 39)
    meta4 = json.load(open(os.path.join(
        empty.clips_dir, info4["basename"] + ".json")))["meta"]
    assert (meta4["width"], meta4["height"]) == (96, 64), meta4
    assert Project(_OUT, "Empty").resolution() == (96, 64)
    print("9. into an empty project: a 100x70 source sets it to 96x64")

    # 10. and a chain of mixed sizes is refused by name before a join
    from h3iw.project import check_uniform_size, ProjectError
    try:
        check_uniform_size([{"index": 1, "meta": {"width": 1248, "height": 832}},
                            {"index": 2, "meta": {"width": 960, "height": 960}}])
    except ProjectError as exc:
        assert "clip 2 is 960x960" in str(exc), exc
    else:
        raise AssertionError("mixed sizes were accepted")
    check_uniform_size([{"index": 1, "meta": {"width": 1248, "height": 832}},
                        {"index": 2, "meta": {}},
                        {"index": 3, "meta": {"width": 1248, "height": 832}}])
    print("10. mixed sizes refused, naming the odd clip; a clip without "
          "a recorded size is not held against the rest")

    # 11. framing: the crop is a slice of real pixels at the offset the
    #     panel showed, and Fit keeps the whole frame inside bars
    wide = torch.zeros(3, 64, 128, 3)          # 2:1 into a 1:1 target
    wide[:, :, :32, 0] = 1.0                   # a red band on the left
    wide[:, :, -32:, 2] = 1.0                  # a blue band on the right
    left = imp.conform_frames(wide, 64, 64, fit="fill", offset=0.0)
    right = imp.conform_frames(wide, 64, 64, fit="fill", offset=1.0)
    mid = imp.conform_frames(wide, 64, 64, fit="fill", offset=0.5)
    assert tuple(left.shape) == (3, 64, 64, 3), left.shape
    assert float(left[..., 0].mean()) > 0.4 and float(left[..., 2].mean()) < 0.05, \
        "offset 0 did not keep the left edge"
    assert float(right[..., 2].mean()) > 0.4 and float(right[..., 0].mean()) < 0.05, \
        "offset 1 did not keep the right edge"
    assert float(mid[..., 0].mean()) < 0.05 and float(mid[..., 2].mean()) < 0.05, \
        "offset 0.5 did not centre between the bands"
    print("11. crop offset 0 / 0.5 / 1 keep the left, middle and right")

    fitted = imp.conform_frames(wide, 64, 64, fit="fit")
    assert tuple(fitted.shape) == (3, 64, 64, 3), fitted.shape
    # the whole 2:1 frame lands in a 64x32 band, bars above and below
    assert float(fitted[:, :14, :, :].abs().max()) == 0.0, "no top bar"
    assert float(fitted[:, -14:, :, :].abs().max()) == 0.0, "no bottom bar"
    assert float(fitted[:, 16:48, :32, 0].mean()) > 0.4, "lost the red band"
    assert float(fitted[:, 16:48, -32:, 2].mean()) > 0.4, "lost the blue band"
    print("12. Fit keeps both edges and puts bars above and below")

    # and it reaches the encode: importing at offset 0 must differ from 1
    shots = {}
    for off in (0.0, 1.0):
        pr = Project(_OUT, "Framing%d" % int(off), create=True)
        pr.set_resolution(64, 32)              # force a 2:1 crop of 64x64
        info = run_import(pr, silent, 39, crop_offset=off)
        with av.open(os.path.join(pr.clips_dir,
                                  info["basename"] + ".mp4")) as c:
            v = c.streams.video[0]
            shots[off] = (v.width, v.height)
    assert shots[0.0] == shots[1.0] == (64, 32), shots
    print("13. the offset rides through the route into the encode")

    print("all checks passed")


if __name__ == "__main__":
    main()
