"""Prove the workflow/prompt actually survive into the mp4.

This one needs real PyAV, because the failure it guards is a muxer
behaviour, not a code path: mov/mp4 writes only its fixed standard tag
set unless `use_metadata_tags` is on, and drops custom keys SILENTLY.
Every stub-based test in this repo passed while the shipped files
carried nothing, which is exactly the class of bug a mock cannot catch.

Skips (exit 0) when PyAV is unavailable so it can sit in the normal run.
"""

import json
import os
import sys
import tempfile

try:
    import av  # noqa: F401
    import numpy as np
except ImportError:
    print("PyAV or numpy unavailable; skipping mp4 metadata probe")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # import the writer directly; it has no ComfyUI dependencies
    import importlib.util
    import types
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("folder_paths", "torch"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
    pkg = types.ModuleType("h3mp"); pkg.__path__ = [pkg_dir]
    sys.modules["h3mp"] = pkg
    for sub in ("project",):
        sp = importlib.util.spec_from_file_location(
            "h3mp." + sub, os.path.join(pkg_dir, sub + ".py"))
        m = importlib.util.module_from_spec(sp)
        sys.modules["h3mp." + sub] = m
        sp.loader.exec_module(m)
    src = open(os.path.join(pkg_dir, "project_nodes.py"),
               encoding="utf-8").read()
    # lift _write_video out on its own: it depends only on av + numpy, so
    # the probe needs no ComfyUI at all
    start = src.index("def _write_video(")
    end = src.index("class H3ProjectSave")
    body = "import av\nimport os\n" + src[start:end]
    ns = {}
    exec(compile(body, "project_nodes.py", "exec"), ns)
    write_video = ns["_write_video"]

    out = tempfile.mkdtemp()
    path = os.path.join(out, "clip_001_take1.mp4")

    frames = 12
    images = np.linspace(0, 1, frames * 64 * 64 * 3, dtype=np.float32)
    images = images.reshape(frames, 64, 64, 3)

    class Wave:
        def __init__(self, a):
            self.a = a
            self.shape = a.shape

        def cpu(self):
            return self

        def numpy(self):
            return self.a
    sr = 32000
    audio = {"waveform": Wave(np.zeros((1, 2, sr // 2), np.float32)),
             "sample_rate": sr}

    workflow = {"nodes": [{"id": i, "type": "Node%d" % i}
                          for i in range(40)]}
    prompt = {"7": {"class_type": "KSampler", "inputs": {"seed": 1234}}}
    tags = {
        "title": "MyProject clip_001_take1",
        "comment": json.dumps({"project": "MyProject",
                               "clip": "clip_001_take1"}),
        "workflow": json.dumps(workflow, separators=(",", ":")),
        "prompt": json.dumps(prompt, separators=(",", ":")),
    }
    write_video(path, images, audio, 24, tags)
    assert os.path.isfile(path)
    print("1. wrote %s (%d KB)" % (os.path.basename(path),
                                   os.path.getsize(path) // 1024))

    with av.open(path) as c:
        meta = dict(c.metadata)
        vstreams = [s for s in c.streams if s.type == "video"]
        astreams = [s for s in c.streams if s.type == "audio"]
        nframes = sum(1 for _ in c.decode(video=0))

    missing = [k for k in tags if k not in meta]
    assert not missing, "muxer dropped: %s" % missing
    print("2. all tags survived the muxer: %s" % ", ".join(sorted(meta)))

    assert json.loads(meta["workflow"]) == workflow, "workflow corrupted"
    assert json.loads(meta["prompt"]) == prompt, "prompt corrupted"
    print("3. workflow (%d nodes) and prompt round-trip byte-exact"
          % len(workflow["nodes"]))

    assert vstreams and astreams, "missing a stream"
    assert nframes == frames, (nframes, frames)
    print("4. %d video frames + %d audio stream decoded back"
          % (nframes, len(astreams)))

    # faststart: moov must precede mdat or the panel stalls at every join
    with open(path, "rb") as fh:
        head = fh.read(4096)
    assert b"moov" in head and (
        b"mdat" not in head or head.index(b"moov") < head.index(b"mdat")), \
        "moov atom is not at the front; faststart did not apply"
    print("5. moov atom is at the front (faststart applied)")

    print("all checks passed")


if __name__ == "__main__":
    main()
