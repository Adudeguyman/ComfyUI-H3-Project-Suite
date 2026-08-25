"""Writing a clip must not need the clip's size in temporaries.

The frame conversion used to run clip(), *255, round() and astype over
the whole batch, making four full-size arrays. A short clip fit; a
longer one pushed the machine into swap, which presents as a ten minute
encode with an idle CPU rather than as an out-of-memory error - so the
symptom points away from the cause.

This counts the largest array the writer allocates while encoding, and
requires it to be frame-sized rather than clip-sized.
"""

import os
import sys
import tempfile

try:
    import av  # noqa: F401
    import numpy as np
except ImportError:
    print("PyAV or numpy unavailable; skipping")
    raise SystemExit(0)


def load_writer():
    import importlib.util
    import types
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("folder_paths", "torch"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
    src = open(os.path.join(pkg, "project_nodes.py"), encoding="utf-8").read()
    start = src.index("def _write_video(")
    end = src.index("class H3ProjectSave")
    body = ("import av\nimport os\nimport logging\n"
            "_LOG = logging.getLogger(\"h3_suite\")\n" + src[start:end])
    ns = {}
    exec(compile(body, "project_nodes.py", "exec"), ns)
    return ns["_write_video"]


class Watcher:
    """numpy allocations, biggest first."""

    def __init__(self, np_mod):
        self.np = np_mod
        self.biggest = 0
        self._real = {}

    def __enter__(self):
        for fn in ("clip", "ascontiguousarray", "asarray"):
            self._real[fn] = getattr(self.np, fn)

            def wrap(orig=self._real[fn]):
                def inner(*a, **k):
                    out = orig(*a, **k)
                    nb = getattr(out, "nbytes", 0)
                    if nb > self.biggest:
                        self.biggest = nb
                    return out
                return inner
            setattr(self.np, fn, wrap())
        return self

    def __exit__(self, *exc):
        for fn, orig in self._real.items():
            setattr(self.np, fn, orig)


def main():
    write_video = load_writer()
    out = tempfile.mkdtemp()

    frames, h, w = 60, 128, 96
    images = np.zeros((frames, h, w, 3), dtype=np.float32)
    images[:] = 0.5
    one_frame = h * w * 3 * 4          # float32 bytes for a single frame
    whole_clip = one_frame * frames

    with Watcher(np) as watch:
        write_video(os.path.join(out, "clip_001_take1.mp4"), images, None,
                    24, {"title": "t"})
    biggest = watch.biggest

    print("clip %d frames = %.1f MB, largest allocation %.1f MB"
          % (frames, whole_clip / 1e6, biggest / 1e6))
    assert biggest < one_frame * 4, (
        "the writer allocated %.1f MB, more than a few frames' worth "
        "(%.1f MB) - it is converting the whole clip at once again"
        % (biggest / 1e6, one_frame * 4 / 1e6))
    assert biggest < whole_clip / 4, (
        "largest allocation %.1f MB is clip-sized, not frame-sized"
        % (biggest / 1e6))
    print("peak allocation stays frame-sized, not clip-sized")

    # and it still produces a correct file
    import av as _av
    with _av.open(os.path.join(out, "clip_001_take1.mp4")) as c:
        got = sum(1 for _ in c.decode(video=0))
    assert got == frames, (got, frames)
    print("%d frames written and read back" % got)

    print("all checks passed")


if __name__ == "__main__":
    main()
