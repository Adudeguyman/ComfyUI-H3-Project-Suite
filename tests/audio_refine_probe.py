"""The audio refine node must hold every video row and free the audio.

A mistake here is expensive and silent: hold the wrong stream and the
refiner rewrites the picture you approved while leaving the audio it was
supposed to fix. So this checks the masks by shape and by value, on the
nested AV latent the node actually receives.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import types  # noqa: E402

import _node_smoke_test as smoke  # noqa: E402
from _mock_harness import make_mm, make_torch  # noqa: E402
from _node_smoke_test import T, Nested  # noqa: E402


def stub_env():
    """The same fake ComfyUI the smoke test builds, minus its assertions."""
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
    mb.MiniMaxH3 = type("MiniMaxH3", (), {"extra_conds": lambda s, **k: {}})
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb
    nh = types.ModuleType("node_helpers")
    nh.conditioning_set_values = lambda c, v, append=False: c
    sys.modules["node_helpers"] = nh
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: "/tmp"
    sys.modules["folder_paths"] = fp


stub_env()

# load the package by file location, same idiom as the smoke test, so the
# relative imports inside nodes.py resolve whatever the folder is called
import importlib.util  # noqa: E402

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "h3mc_pkg", os.path.join(_PKG_DIR, "__init__.py"),
    submodule_search_locations=[_PKG_DIR])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["h3mc_pkg"] = _pkg
_spec.loader.exec_module(_pkg)
nodes = sys.modules["h3mc_pkg.nodes"]


def mk_latent(latent_t=7, h=22, w=38, audio_t=16):
    return {"samples": Nested([
        T(np.arange(1 * 16 * latent_t * h * w, dtype=np.float32
                    ).reshape(1, 16, latent_t, h, w) % 97),
        T(np.zeros((1, 32, 2, audio_t), dtype=np.float32)),
    ])}


def main():
    node = nodes.H3AudioRefine()
    lat = mk_latent()
    out, = node.apply(latent=lat, audio_hold=0.0)

    assert "noise_mask" in out, "no mask attached"
    vm, am = out["noise_mask"].unbind()

    v = lat["samples"].unbind()[0]
    a = lat["samples"].unbind()[1]
    # video mask: one channel, video's own temporal and spatial extent
    assert vm.shape == (v.shape[0], 1, v.shape[2], v.shape[3], v.shape[4]), \
        vm.shape
    assert float(vm.a.max()) == 0.0, \
        "video must be held everywhere, found a free row"
    # audio mask: audio's own axes - T is the LAST axis, not video's dim 2
    assert am.shape == (a.shape[0], 1, a.shape[2], a.shape[3]), am.shape
    assert float(am.a.min()) == 1.0, "audio must be free at hold 0"
    print("hold 0.0: video mask %s all-held, audio mask %s all-free"
          % (str(vm.shape), str(am.shape)))

    # a partial hold nudges rather than redoes
    out2, = node.apply(latent=mk_latent(), audio_hold=0.4)
    _vm2, am2 = out2["noise_mask"].unbind()
    assert abs(float(am2.a.max()) - 0.6) < 1e-6, float(am2.a.max())
    print("hold 0.4: audio mask 0.60 -> refines toward what is there")

    # the video content itself must be untouched: this node only masks
    assert np.array_equal(out["samples"].unbind()[0].a, v.a), \
        "video samples were modified"
    print("video samples passed through unmodified")

    # a plain (non-AV) latent must be refused, not silently mishandled
    try:
        node.apply(latent={"samples": T(np.zeros((1, 16, 7, 22, 38),
                                                 dtype=np.float32))})
    except RuntimeError as exc:
        assert "audio-video latent" in str(exc), exc
        print("plain latent refused: %s" % str(exc)[:58])
    else:
        raise AssertionError("a video-only latent must be refused")

    print("all checks passed")


if __name__ == "__main__":
    main()
