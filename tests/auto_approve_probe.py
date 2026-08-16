"""Auto-approve skips the gate without discarding the history.

The promise made in the UI is specific: renders are approved as they
finish, the chain keeps extending on its own, and every take is still
kept so a clip can be reopened afterwards. Each of those is checked
here, along with the flag surviving a reload and the manifest staying
valid throughout.
"""

import os
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

fp = types.ModuleType("folder_paths")
_OUT = tempfile.mkdtemp()
fp.get_output_directory = lambda: _OUT
sys.modules["folder_paths"] = fp

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "h3p_project", os.path.join(_PKG, "project.py"))
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)
Project = project.Project


def touch(p, basename):
    for ext in (".mp4", ".safetensors"):
        with open(os.path.join(p.clips_dir, basename + ext), "wb") as fh:
            fh.write(b"x")


def main():
    p = Project(_OUT, "Auto", create=True)
    assert p.auto_approve is False, "the gate must start ON"

    # --- with the gate on: a render waits ---
    i, t, b = p.next_save()
    touch(p, b)
    p.record_render(i, t)
    assert p.pending() is not None, "gate on: the render should wait"
    i2, t2, _ = p.next_save()
    assert (i2, t2) == (1, 2), "gate on: queueing again re-rolls clip 1"
    p.approve()
    print("gate on: clip 1 waited, and re-queueing would re-roll it")

    # --- turn it off ---
    p.set_auto_approve(True)
    assert p.auto_approve is True
    i, t, b = p.next_save()
    assert (i, t) == (2, 1), (i, t)
    touch(p, b)
    p.record_render(i, t)
    assert p.pending() is None, "auto: nothing should be pending"
    assert p.clips[-1]["status"] == "approved"
    assert p.clips[-1].get("auto_approved") is True, \
        "an auto-approved clip must be marked as such"
    i3, t3, _ = p.next_save()
    assert (i3, t3) == (3, 1), \
        "auto: the next queue must EXTEND the chain, not re-roll (%d,%d)" \
        % (i3, t3)
    print("auto on: clip 2 approved on arrival, next render is clip 3 take 1")

    # --- the history survives: takes are still recorded ---
    assert p.clips[-1]["takes"] == [
        {"take": 1, "basename": "clip_002_take1", "meta": None}], \
        p.clips[-1]["takes"]
    # and an auto-approved clip can still be reopened for another take
    p.reopen(2)
    assert p.pending() is not None and p.pending()["index"] == 2
    i4, t4, b4 = p.next_save()
    assert (i4, t4) == (2, 2), (i4, t4)
    touch(p, b4)
    p.record_render(i4, t4)
    takes = [t["take"] for t in p.takes_of(2)]
    assert takes == [1, 2], "both takes must survive auto-approval: %s" % takes
    print("auto on: clip 2 reopened and re-rolled, takes %s both kept"
          % takes)

    # --- the flag persists across a reload ---
    q = Project(_OUT, "Auto")
    assert q.auto_approve is True, "the flag must survive a reload"
    q.set_auto_approve(False)
    r = Project(_OUT, "Auto")
    assert r.auto_approve is False, "turning it back on must persist too"
    print("flag persists across reloads, both ways")

    # --- and with it off again, the gate is back ---
    # note the re-rolled take was itself auto-approved on arrival, so
    # nothing is waiting at this point - that IS the feature
    assert r.pending() is None, "auto-approve should have left nothing open"
    i5, t5, b5 = r.next_save()
    touch(r, b5)
    r.record_render(i5, t5)
    assert r.pending() is not None, "gate off again: renders must wait"
    print("gate restored: the next render waits for review again")

    print("all checks passed")


if __name__ == "__main__":
    main()
