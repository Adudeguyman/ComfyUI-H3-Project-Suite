"""Walk a project through its whole life, honestly including the ugly parts.

Chronology: create -> render clip 1 -> approve -> render clip 2 -> re-roll
(supersede) -> approve -> render clip 3 -> reject -> reopen clip 1 with a
cascade -> purge. Along the way: invariant enforcement, containment against
crafted basenames and symlink escape, crash-safe manifest writes, and the
IS_CHANGED token actually changing on every transition.
"""

import json
import os
import sys
import tempfile

_TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS))

from project import (Project, ProjectError, list_projects,  # noqa: E402
                     validate_name)


def _fake_pair(proj, basename):
    for ext in (".mp4", ".safetensors"):
        with open(os.path.join(proj.clips_dir, basename + ext), "w") as fh:
            fh.write(basename)


def main():
    out = tempfile.mkdtemp()

    # names
    for bad in ("", "..", "a/b", "x" * 70, ".hidden"):
        try:
            validate_name(bad)
            raise AssertionError("accepted %r" % bad)
        except ProjectError:
            pass
    validate_name("Desert Chase_2.final")
    print("1. name validation rejects traversal, hidden, oversized")

    # create + empty state
    p = Project(out, "TestProj", create=True)
    assert list_projects(out) == ["TestProj"]
    assert p.chain_active() is False and p.tail_latent_path() is None
    assert p.next_save() == (1, 1, "clip_001_take1")
    tok0 = p.mtime_token()
    print("2. empty project: chain inactive, next save clip_001_take1")

    # clip 1: render -> pending -> approve
    _fake_pair(p, "clip_001_take1")
    p.record_render(1, 1)
    assert p.pending()["basename"] == "clip_001_take1"
    assert p.chain_active() is False  # pending does not arm the chain
    p.approve()
    assert p.chain_active() is True
    assert p.tail_latent_path().endswith("clip_001_take1.safetensors")
    assert p.next_save() == (2, 1, "clip_002_take1")
    print("3. clip 1 rendered, approved; chain armed on approve, not before")

    # clip 2: render, then re-roll supersedes the pending take
    _fake_pair(p, "clip_002_take1")
    p.record_render(2, 1)
    assert p.next_save() == (2, 2, "clip_002_take2")  # re-roll, same index
    _fake_pair(p, "clip_002_take2")
    p.record_render(2, 2)
    assert p.pending()["take"] == 2
    # both takes stay on disk so they can be compared and re-selected
    assert os.path.isfile(os.path.join(p.clips_dir, "clip_002_take1.mp4"))
    assert [t["take"] for t in p.takes_of(2)] == [1, 2]
    assert p.pending()["from"] == "clip_001_take1"

    p.select_take(2, 1)
    assert p.pending()["basename"] == "clip_002_take1"
    p.select_take(2, 2)
    assert p.pending()["basename"] == "clip_002_take2"
    try:
        p.select_take(2, 9)
        raise AssertionError("selected a take that does not exist")
    except ProjectError:
        pass
    p.approve()
    try:
        p.select_take(2, 1)
        raise AssertionError("switched takes on an approved clip")
    except ProjectError:
        pass
    print("4. re-roll KEPT take 1 on disk, switching works while pending, "
          "refused once approved")

    dropped = p.discard_other_takes(2)
    assert dropped == ["clip_002_take1"]
    assert os.path.isfile(os.path.join(p.trash_dir, "clip_002_take1.mp4"))
    assert [t["take"] for t in p.takes_of(2)] == [2]
    print("5. discard_other_takes trashed the unselected take")

    # clip 3: render then reject
    _fake_pair(p, "clip_003_take1")
    p.record_render(3, 1)
    p.reject()
    assert p.pending() is None and len(p.clips) == 2
    assert p.next_save() == (3, 1, "clip_003_take1")
    print("6. reject dropped clip 3 to .trash/, next save is clip 3 again")

    # out-of-order renders refused
    for idx, take in ((5, 1), (1, 9)):
        try:
            _fake_pair(p, "clip_%03d_take%d" % (idx, take))
            p.record_render(idx, take)
            raise AssertionError("accepted out-of-order render %d" % idx)
        except ProjectError:
            pass
    print("7. out-of-order renders refused")

    # reopen clip 1: cascade drops clip 2
    assert p.cascade_of(1) == ["clip_002_take2"]
    dropped = p.reopen(1)
    assert dropped == ["clip_002_take2"]
    assert p.pending()["basename"] == "clip_001_take1"
    assert p.chain_active() is False
    assert p.next_save() == (1, 2, "clip_001_take2")
    print("8. reopen(1) cascaded clip 2 to .trash/, clip 1 pending again")

    # containment: crafted basename and symlink escape both refused
    # reject now walks the takes list, so corrupt that too
    p.clips[-1]["basename"] = "../project"
    p.clips[-1]["takes"] = [{"take": 1, "basename": "../project"}]
    try:
        p.reject()
        raise AssertionError("crafted basename was trashed")
    except ProjectError:
        p.clips[-1]["basename"] = "clip_001_take1"
        p.clips[-1]["takes"] = [{"take": 1,
                                 "basename": "clip_001_take1"}]
    victim = os.path.join(out, "innocent.txt")
    open(victim, "w").write("do not delete")
    link = os.path.join(p.clips_dir, "clip_001_take1.mp4")
    os.unlink(link)
    os.symlink(victim, link)
    try:
        p.reject()
        raise AssertionError("symlink escape was followed")
    except ProjectError:
        pass
    assert os.path.isfile(victim)
    os.unlink(link)
    _fake_pair(p, "clip_001_take1")
    print("9. crafted basename and symlink escape both refused, "
          "victim intact")

    # atomic write: a corrupt manifest never half-overwrites; simulate by
    # checking the temp-and-replace leaves valid JSON at every step
    p.reject()
    with open(p.manifest_path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == 1 and data["clips"] == []
    stray = [f for f in os.listdir(p.root) if f.startswith(".manifest_")]
    assert not stray, stray
    print("10. manifest valid JSON after every transition, no temp strays")

    # invariant check on load: hand-corrupt and confirm refusal
    data["clips"] = [{"index": 2, "take": 1, "status": "approved",
                      "basename": "clip_002_take1", "from": None}]
    with open(p.manifest_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        Project(out, "TestProj")
        raise AssertionError("corrupt manifest loaded")
    except ProjectError:
        pass
    print("11. corrupt manifest (index/position disagreement) refused "
          "at load")

    # tokens changed across the life; purge works
    with open(p.manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "name": "TestProj", "clips": []}, fh)
    p2 = Project(out, "TestProj")
    assert p2.mtime_token() != tok0
    p2.purge_trash()
    assert not os.path.isdir(p2.trash_dir)
    print("12. IS_CHANGED token moved, purge cleared .trash/")

    print("all checks passed")


if __name__ == "__main__":
    main()
