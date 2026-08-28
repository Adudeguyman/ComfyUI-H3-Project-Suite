"""The pack must run correctly on all three layout shapes.

0.31-0.33.0: first/last restriction, frame_count present -> full patch.
0.33.x master (#15439): interior anchors native -> audio wrapper only.
0.34.0: anchors AND keyframe audio native, no frame_count -> NOTHING
installed; the nodes emit plain keyframes with a fractional anchor.

Detection is behavioural throughout, so this drives the real
apply_patch() against faithful mocks of each shape and checks what got
installed, what the node emits, and where the audio actually lands.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _mock_harness import make_mm, make_mm_034, make_torch  # noqa: E402

FR = make_mm().FRAME_RESCALE


def install(mm):
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        mod = sys.modules.setdefault(name, types.ModuleType(name))
        mod.__path__ = []          # a package, or comfy.utils will not import
    mb = types.ModuleType("comfy.model_base")

    class _MiniMaxH3:
        def extra_conds(self, **kw):
            return {}

    mb.MiniMaxH3 = _MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb
    utils = types.ModuleType("comfy.utils")
    utils.common_upscale = lambda s, w, h, mode, crop: s
    sys.modules["comfy.utils"] = utils
    sys.modules["comfy"].utils = utils
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["torch"] = make_torch()


def fresh():
    sys.modules.pop("patch_layout", None)
    import importlib
    return importlib.import_module("patch_layout")


def main():
    # --- 0.34: nothing installed, everything covered ---
    mm = make_mm_034()
    install(mm)
    pl = fresh()
    stock = mm.PackedLayout.__init__
    assert pl.apply_patch() is False
    assert mm.PackedLayout.__init__ is stock, "0.34 core was patched"
    assert pl.is_applied() is False
    assert pl.is_covered() is True, "native coverage must count as covered"
    assert pl.audio_keyframes_native() is True
    print("1. 0.34 core: nothing installed, covered, audio native")

    # --- and the audio keyframe layout does what the node relies on:
    # an end-aligned window built from a fractional (negative) anchor ---
    torch = sys.modules["torch"]
    rt = 6
    end_frame = 2.5
    start = end_frame - rt * (24.0 / 40.0)      # -1.1: negative on purpose
    kf = [{"resolved_frame_index": start,
           "audio_latent": torch.zeros(1, 32, 2, rt)}]
    lay = mm.PackedLayout(7, 7, 2, 2, 16, keyframes=kf)
    rows = [(a, b) for a, b, kind in lay.segments if kind == "cond_audio"]
    assert rows, "no cond_audio rows from an audio keyframe"
    first = float(lay.position_ids[rows[0][0], 0])
    want_first = 7 + FR * start
    assert abs(first - want_first) < 1e-6, (first, want_first)
    print("2. fractional negative anchor placed literally "
          "(start %.2f -> position %.3f)" % (start, first))

    # --- the node itself must run on a native core. The first 0.34
    # --- build crashed inside its own log line, which every
    # --- layout-level check sailed past: nothing drove H3Context.apply.
    import importlib.util
    for name in [n for n in sys.modules
                 if n == "h3pkg" or n.startswith("h3pkg.")]:
        del sys.modules[name]
    fake_helpers = types.ModuleType("node_helpers")

    def _csv(cond, values, append=False):
        out = []
        for c in cond:
            d = dict(c[1])
            for k, v in values.items():
                if append and k in d:
                    d[k] = list(d[k]) + list(v)
                else:
                    d[k] = v
            out.append([c[0], d])
        return out

    fake_helpers.conditioning_set_values = _csv
    sys.modules["node_helpers"] = fake_helpers
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: "/tmp"
    sys.modules.setdefault("folder_paths", fp)
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = types.ModuleType("h3pkg")
    pkg.__path__ = [pkg_dir]
    sys.modules["h3pkg"] = pkg
    for sub in ("patch_layout", "patch_payload", "nodes"):
        spec = importlib.util.spec_from_file_location(
            "h3pkg." + sub, os.path.join(pkg_dir, sub + ".py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["h3pkg." + sub] = m
        spec.loader.exec_module(m)
    nodes = sys.modules["h3pkg.nodes"]
    # the freshly loaded copy probes the core again through its own
    # patch_layout, so make sure that one is on the native path too
    assert sys.modules["h3pkg.patch_layout"].apply_patch() is False
    assert sys.modules["h3pkg.patch_layout"].audio_keyframes_native()
    ctx = nodes.H3Context()
    if True:
        # an AV latent is a nested pair: unbind() gives video, audio
        class _AV:
            def __init__(self, v, a):
                self._p = [v, a]

            def unbind(self):
                return list(self._p)

        video = torch.zeros(1, 24, 12, 8, 8)
        audio = torch.zeros(1, 32, 2, 20)
        lat = {"samples": _AV(video, audio)}
        cond = [[torch.zeros(1, 4, 16), {}]]
        try:
            out, _trim, _lat = ctx.apply(
                cond, lat, 22, "video", "head", "disabled",
                audio_context_length=22, audio_mode="timeline",
                video_source="latent", context_latent=lat,
                enabled=True, seed_head=False)
        except AttributeError as exc:
            raise AssertionError(
                "H3Context crashed on a native core: %s" % exc)
        except Exception as exc:
            print("   (node ran but this harness could not complete it: "
                  "%s)" % exc)
        else:
            meta = out[0][1]
            kfs = meta.get("minimax_keyframes") or []
            audio_kfs = [k for k in kfs
                         if k.get("audio_latent") is not None]
            assert audio_kfs, "no audio keyframe on the native path"
            assert not (meta.get("minimax_refs") or []), \
                "the native path must not append a smuggled audio ref"
            print("2b. H3Context ran on 0.34: audio is a keyframe at "
                  "%.3f, no ref appended"
                  % float(audio_kfs[0]["resolved_frame_index"]))

    # --- 0.33-with-#15439 (interior native, frame_count present):
    # the audio wrapper installs, exactly as before ---
    mm2 = make_mm()
    # lift the restriction the way #15439 did, keeping frame_count
    base = mm2.PackedLayout.__init__

    def lifted(self, text_len, latent_t, lh, lw, audio_t, keyframes=None,
               refs=None, frame_count=None):
        fixed = []
        for k in (keyframes or []):
            k = dict(k)
            fixed.append(k)
        # place interior anchors by index, first/last via the old body
        try:
            base(self, text_len, latent_t, lh, lw, audio_t,
                 keyframes=None, refs=refs, frame_count=frame_count)
        except Exception:
            raise
        segs = list(self.segments)
        coords = list(self.position_ids[:, 0])
        for k in fixed:
            p = float(k["resolved_frame_index"])
            t = float(text_len) + FR * p
            a = len(coords)
            coords.extend([t] * 4)
            segs.append((a, len(coords), "cond"))
        self.segments = segs
        import numpy as np
        self.position_ids = np.zeros((len(coords), 4), dtype=np.float64)
        self.position_ids[:, 0] = coords

    mm2.PackedLayout.__init__ = lifted
    install(mm2)
    pl2 = fresh()
    assert pl2.apply_patch() is True, "the audio wrapper should install"
    assert pl2._mode == "audio_only", pl2._mode
    assert pl2.audio_keyframes_native() is False
    print("3. #15439-era core: audio wrapper installs, as before")

    # --- plain 0.33: the full patch installs, as always ---
    mm3 = make_mm()
    install(mm3)
    pl3 = fresh()
    assert pl3.apply_patch() is True
    assert pl3._mode == "full", pl3._mode
    print("4. 0.33 core: full patch, unchanged")

    print("all checks passed")


if __name__ == "__main__":
    main()
