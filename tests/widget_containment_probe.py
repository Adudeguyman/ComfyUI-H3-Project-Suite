"""The two widget paths stay inside ComfyUI's output folder.

A node widget is free text on a workflow that anyone who can reach
/prompt may queue, so `latent_path` on the Load node and
`filename_prefix` on the Save node are untrusted. Ordinary values must
pass through untouched; anything that resolves outside the output
folder must raise, not be quietly rewritten; and a symlink inside the
tree that points out of it counts as outside. At the end the check is
disabled in place and the escaping save is re-run: it must then get
through, proving the probe measures the check.
"""

import os
import sys
import tempfile
import types

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

from _mock_harness import make_mm, make_torch  # noqa: E402


class T:
    def __init__(self, a):
        self.a = np.asarray(a)

    @property
    def shape(self):
        return self.a.shape

    def cpu(self):
        return self

    def contiguous(self):
        return T(np.ascontiguousarray(self.a))


class Nested:
    def __init__(self, parts):
        self.parts = parts

    def unbind(self):
        return list(self.parts)


def load_nodes(outdir):
    mm = make_mm()
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    sys.modules["torch"] = make_torch()
    cu = types.ModuleType("comfy.utils")
    sys.modules["comfy.utils"] = cu
    sys.modules["comfy"].utils = cu
    mb = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **kw):
            return {}
    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    sys.modules["comfy"].model_base = mb
    nh = types.ModuleType("node_helpers")
    nh.conditioning_set_values = lambda c, v, append=False: c
    sys.modules["node_helpers"] = nh

    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: outdir

    def get_save_image_path(prefix, out, *a):
        # deliberately trusting: joins whatever it is given, so the
        # pack's own check is the only thing standing in the way
        sub, name = os.path.split(prefix)
        folder = os.path.join(out, sub)
        os.makedirs(folder, exist_ok=True)
        return folder, name, 1, sub, prefix
    fp.get_save_image_path = get_save_image_path
    sys.modules["folder_paths"] = fp

    st = types.ModuleType("safetensors")
    stt = types.ModuleType("safetensors.torch")
    written = []

    def save_file(d, path, metadata=None):
        written.append(path)
        open(path, "w").write("latent")
    stt.save_file = save_file
    stt.load_file = lambda path: {}
    st.torch = stt
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = stt

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "h3wc_pkg", os.path.join(_PKG_DIR, "__init__.py"),
        submodule_search_locations=[_PKG_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["h3wc_pkg"] = pkg
    spec.loader.exec_module(pkg)
    return sys.modules["h3wc_pkg.nodes"], written


def main():
    outdir = os.path.realpath(tempfile.mkdtemp())
    elsewhere = os.path.realpath(tempfile.mkdtemp())
    nodes, written = load_nodes(outdir)
    contain = nodes._contain_prefix
    resolve = nodes._resolve_latent_path

    # 1. ordinary prefixes pass through byte-identical
    for p in ("h3_context/clip", "clip", "a..b/clip", "deep/er/name.v2",
              "h3_context/clip 2"):
        assert contain(p) == p, (p, contain(p))
    print("1. ordinary prefixes untouched (including a..b)")

    # 2. escaping prefixes raise, in every spelling
    for p in ("../clip", "h3_context/../../clip", "..", "x/..",
              "..\\clip", "h3_context\\..\\..\\clip"):
        try:
            contain(p)
        except ValueError:
            continue
        raise AssertionError("%r was accepted" % p)
    print("2. '..' in any position or spelling raises")

    # 3. absolute, drive-letter and UNC spellings become relative
    assert contain("/h3_context/clip") == "h3_context/clip"
    assert contain("C:\\h3_context\\clip") == "h3_context/clip"
    assert contain("//server/share/clip") == "server/share/clip"
    print("3. absolute / drive / UNC prefixes normalise to relative")

    # 4. a symlink inside the output tree that points out of it
    os.symlink("/", os.path.join(outdir, "esc"))
    try:
        contain("esc/tmp/clip")
    except ValueError:
        pass
    else:
        raise AssertionError("symlink escape accepted")
    print("4. symlink out of the tree refused")

    # 5. the Save node: an escaping prefix writes nothing anywhere
    latent = {"samples": Nested([T(np.zeros((1, 16, 2, 4, 4), np.float32)),
                                 T(np.zeros((1, 32, 2, 4), np.float32))])}
    node = nodes.H3ContextSaveLatent()
    try:
        node.save(latent, "../evil", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("save accepted an escaping prefix")
    assert not written, written
    (path,) = node.save(latent, "h3_context/clip", 1)
    assert written == [path] and path.startswith(outdir + os.sep), path
    print("5. Save node: '../evil' writes nothing; a normal prefix saves "
          "under the output folder")

    # 6. the Load node's path: inside the output folder in either form,
    #    never anywhere else
    os.makedirs(os.path.join(elsewhere, "h3_context"))
    open(os.path.join(elsewhere, "h3_context", "clip_00001.safetensors"),
         "w").write("x")
    assert resolve("h3_context", 1) == path
    assert resolve(os.path.join(outdir, "h3_context"), 1) == path
    assert resolve(path, 1) == path
    for bad in (os.path.join(elsewhere, "h3_context"),
                os.path.join(elsewhere, "h3_context", "clip_00001.safetensors"),
                "../" + os.path.basename(elsewhere) + "/h3_context",
                "esc" + elsewhere + "/h3_context"):
        try:
            resolve(bad, 1)
        except ValueError:
            continue
        raise AssertionError("%r was resolved" % bad)
    print("6. Load node: output folder only; absolute, ../ and symlink "
          "escapes refused")

    # 7. disable the check: the escaping save must now get through,
    #    which is the proof this probe measures the check and not luck
    written.clear()
    orig = nodes._contain_prefix
    nodes._contain_prefix = lambda p: p
    try:
        node.save(latent, "../evil", 1)
        assert written, "nothing written with the check disabled"
        landed = os.path.realpath(written[0])
        assert not landed.startswith(outdir + os.sep), landed
    finally:
        nodes._contain_prefix = orig
        for w in written:
            try:
                os.unlink(w)
            except OSError:
                pass
    print("7. check disabled -> the escape lands outside; restored")

    print("all checks passed")


if __name__ == "__main__":
    main()
