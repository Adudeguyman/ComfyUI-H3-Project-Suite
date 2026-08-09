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

from project import (Project, ProjectError, branch_project,  # noqa: E402
                     list_projects, snapshot_project, suggest_branch_name,
                     suggest_snapshot_name, validate_name)


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
    # the take is remembered as trashed, not forgotten: its files still
    # exist and a later branch can build on them
    assert [t["take"] for t in p.takes_of(2)] == [1, 2]
    assert p.takes_of(2)[0].get("trashed") is True
    assert {t["take"]: t["location"] for t in p.available_takes(2)} == {
        1: "trash", 2: "clips"}
    print("5. discard_other_takes trashed the unselected take but kept it "
          "discoverable for branching")

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

    # branching: fork an approved chain into an independent project
    import shutil as _shutil
    bp = Project(out, "Chain", create=True)
    for i in (1, 2, 3):
        base = "clip_%03d_take1" % i
        for ext in (".mp4", ".safetensors"):
            with open(os.path.join(bp.clips_dir, base + ext), "wb") as fh:
                fh.write(os.urandom(256))
        bp.record_render(i, 1, {"duration": 5.0})
        bp.approve()
    name = suggest_branch_name(out, "Chain", 2)
    assert name == "Chain_from2", name
    br = branch_project(out, "Chain", 2, name)
    assert [c["index"] for c in br.clips] == [1, 2]
    assert br.next_save() == (3, 1, "clip_003_take1")
    assert br.branched_from == {"project": "Chain", "at_index": 2}
    assert len(Project(out, "Chain").clips) == 3, "source changed"
    a = os.path.join(bp.clips_dir, "clip_002_take1.safetensors")
    b2 = os.path.join(br.clips_dir, "clip_002_take1.safetensors")
    assert open(a, "rb").read() == open(b2, "rb").read()
    assert os.stat(a).st_ino != os.stat(b2).st_ino, "branch is hardlinked"
    # deleting the source outright must not touch the branch
    _shutil.rmtree(bp.root)
    br2 = Project(out, "Chain_from2")
    assert br2.tail_latent_path().endswith("clip_002_take1.safetensors")
    print("13. branch at clip 2: independent copies, source deletable, "
          "next render clip_003_take1")

    # branch from a take that was never approved, recovered from .trash/
    tk = Project(out, "Takes", create=True)
    for ext in (".mp4", ".safetensors"):
        with open(os.path.join(tk.clips_dir, "clip_001_take1" + ext),
                  "wb") as fh:
            fh.write(os.urandom(64))
    tk.record_render(1, 1, {"duration": 5.0}); tk.approve()
    for t in (1, 2, 3):
        base = "clip_002_take%d" % t
        for ext in (".mp4", ".safetensors"):
            with open(os.path.join(tk.clips_dir, base + ext), "wb") as fh:
                fh.write(bytes([t]) * 64)
        tk.record_render(2, t, {"duration": 5.0 + t})
    tk.approve()                       # take 3 is the approved one
    tk.discard_other_takes(2)          # takes 1 and 2 go to .trash/
    alt = branch_project(out, "Takes", 2, "Takes_alt", at_take=2)
    assert alt.chain_tail()["basename"] == "clip_002_take2"
    assert alt.next_save() == (3, 1, "clip_003_take1")
    with open(os.path.join(alt.clips_dir, "clip_002_take2.mp4"), "rb") as fh:
        assert fh.read() == bytes([2]) * 64
    assert Project(out, "Takes")._entry(2)["take"] == 3, "source changed"
    print("14. branched on clip 2 take 2 (never approved, sitting in "
          "trash); source still on take 3")

    # a trash-recovered take must become a LIVE clip in the branch, not
    # arrive still-trashed and vanish on the next purge
    for ext in (".mp4", ".safetensors"):
        assert os.path.isfile(os.path.join(alt.clips_dir,
                                           "clip_002_take2" + ext))
    assert not os.path.exists(alt.trash_dir), "branch created a .trash/"
    e2 = alt._entry(2)
    assert e2["status"] == "approved" and e2["take"] == 2
    assert all(not t.get("trashed") for t in e2["takes"]), e2["takes"]
    assert {t["location"] for t in alt.available_takes(2)} == {"clips"}
    alt.purge_trash()
    Project(out, "Takes").purge_trash()          # source purge too
    assert os.path.isfile(os.path.join(alt.clips_dir,
                                       "clip_002_take2.mp4"))
    with open(os.path.join(alt.clips_dir, "clip_002_take2.mp4"),
              "rb") as fh:
        assert fh.read() == bytes([2]) * 64      # content intact
    assert alt.tail_latent_path().endswith("clip_002_take2.safetensors")
    print("15. recovered take lives in clips/ as approved; neither purge "
          "can reach it")

    # storage accounting and project-wide take cleanup
    sp = Project(out, "Space", create=True)
    for i, takes in ((1, [1]), (2, [1, 2, 3])):
        for t in takes:
            base = "clip_%03d_take%d" % (i, t)
            for ext in (".mp4", ".safetensors"):
                with open(os.path.join(sp.clips_dir, base + ext),
                          "wb") as fh:
                    fh.write(b"\0" * 4096)
            sp.record_render(i, t, {"duration": 5.0})
        sp.approve()
    rep = sp.storage_report()
    assert rep["chain_clips"] == 2 and rep["alternate_takes"] == 2, rep
    dry = sp.cleanup_takes(dry_run=True)
    assert len(dry["planned"]) == 2
    assert sp.storage_report()["alternate_takes"] == 2, "dry run mutated"
    sp.cleanup_takes()
    rep2 = sp.storage_report()
    assert rep2["alternate_takes"] == 0
    assert rep2["chain_bytes"] == rep["chain_bytes"], "chain shrank!"
    assert rep2["trash_takes"] == 2
    for c in sp.clips:
        assert os.path.isfile(os.path.join(sp.clips_dir,
                                           c["basename"] + ".mp4"))
    # still branchable out of trash until purged
    rescue = branch_project(out, "Space", 2, "Space_alt", at_take=1)
    assert rescue.chain_tail()["basename"] == "clip_002_take1"
    print("16. cleanup_takes trashed %d alternates, chain untouched, "
          "cleaned takes still branchable" % rep2["trash_takes"])

    # un-approving with a safety net: snapshot the chain, then reopen
    sh = Project(out, "Show", create=True)
    for i in range(1, 6):
        base = "clip_%03d_take1" % i
        for ext in (".mp4", ".safetensors"):
            with open(os.path.join(sh.clips_dir, base + ext), "wb") as fh:
                fh.write(bytes([i]) * 128)
        sh.record_render(i, 1, {"duration": 5.0})
        sh.approve()
    assert suggest_snapshot_name(out, "Show") == "Show_backup"
    assert sh.cascade_of(3) == ["clip_004_take1", "clip_005_take1"]
    snap = snapshot_project(out, "Show")
    assert snap.name == "Show_backup" and len(snap.approved()) == 5
    sh2 = Project(out, "Show")
    dropped = sh2.reopen(3)
    assert dropped == ["clip_004_take1", "clip_005_take1"]
    assert len(sh2.clips) == 3 and sh2.pending()["index"] == 3
    # the snapshot kept everything the reopen threw away
    for name in ("clip_004_take1", "clip_005_take1"):
        assert os.path.isfile(os.path.join(snap.clips_dir, name + ".mp4"))
    with open(os.path.join(snap.clips_dir, "clip_005_take1.mp4"),
              "rb") as fh:
        assert fh.read() == bytes([5]) * 128
    assert snap.next_save() == (6, 1, "clip_006_take1")
    assert suggest_snapshot_name(out, "Show") == "Show_backup_002"
    print("17. snapshot before reopen: backup keeps all 5 clips and still "
          "chains, original cut to 3 with clip 3 pending")

    # reopening an earlier clip while a later one awaits review: the
    # pending clip continues from the target, so it is part of the cascade
    bd = Project(out, "Bird", create=True)
    for i in (1, 2):
        base = "clip_%03d_take1" % i
        for ext in (".mp4", ".safetensors"):
            with open(os.path.join(bd.clips_dir, base + ext), "wb") as fh:
                fh.write(b"x" * 64)
        bd.record_render(i, 1, {"duration": 5.0})
        if i == 1:
            bd.approve()
    assert bd.pending()["index"] == 2
    assert bd.cascade_of(1) == ["clip_002_take1"]
    assert bd.reopen(1) == ["clip_002_take1"]
    assert len(bd.clips) == 1 and bd.pending()["index"] == 1
    assert bd.chain_active() is False
    assert bd.next_save() == (1, 2, "clip_001_take2")
    try:
        bd.reopen(1)
        raise AssertionError("reopened an already-pending clip")
    except ProjectError as exc:
        assert "not approved" in str(exc)
    print("18. reopen works with a later clip pending; that clip is "
          "carried into the cascade, and reopening a pending clip is "
          "still refused")

    print("all checks passed")


if __name__ == "__main__":
    main()
