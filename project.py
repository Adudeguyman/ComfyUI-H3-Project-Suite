"""Project manifests: the single source of truth for a clip chain.

A project is one folder under <output>/h3_projects/<name>/ holding a
manifest (project.json), a clips/ folder of atomic (mp4, safetensors)
pairs, and a .trash/ folder for rejected pairs. The manifest is a plain
list in chain order -- position IS the clip index, there is no separate
ordering to keep in sync with the conditioning chain:

    {
      "version": 1,
      "name": "DesertChase",
      "clips": [
        {"index": 1, "take": 1, "status": "approved",
         "basename": "clip_001_take1", "from": null},
        {"index": 2, "take": 3, "status": "pending",
         "basename": "clip_002_take3", "from": "clip_001_take1"}
      ]
    }

Invariants this module enforces rather than documents:
  - at most ONE pending clip, and it is always the LAST entry
  - every approved clip's pair (mp4 + safetensors) exists on disk
  - "from" records the basename a clip was conditioned on, making the
    chain self-describing and broken links detectable
  - all destructive operations stay inside the project folder (realpath
    containment + basename pattern check, both, before any move)

Writes are atomic: the manifest is written to a temp file in the same
folder and os.replace()d over the old one, so a crash mid-write leaves
the previous manifest intact rather than a half-written JSON.
"""

import json
import logging
import os
import re
import shutil
import tempfile

_LOG = logging.getLogger("h3_suite")

PROJECTS_DIRNAME = "h3_projects"
MANIFEST_NAME = "project.json"
CLIPS_DIRNAME = "clips"
TRASH_DIRNAME = ".trash"
VERSION = 1

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_BASENAME_RE = re.compile(r"^clip_(\d{3})_take(\d+)$")

VIDEO_EXT = ".mp4"
LATENT_EXT = ".safetensors"
SIDECAR_EXT = ".json"


class ProjectError(RuntimeError):
    """A manifest or filesystem invariant was violated."""


def validate_name(name):
    """A project name must be a safe single path component."""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ProjectError(
            "h3_suite: project name %r is not allowed. Use letters, digits, "
            "spaces, dots, dashes or underscores (max 64 chars, must start "
            "with a letter or digit)." % name)
    if name in (".", "..") or os.sep in name or (os.altsep or "/") in name:
        raise ProjectError("h3_suite: project name %r is not a plain "
                           "folder name." % name)
    return name


def projects_root(output_dir):
    return os.path.join(output_dir, PROJECTS_DIRNAME)


def list_projects(output_dir):
    root = projects_root(output_dir)
    if not os.path.isdir(root):
        return []
    out = []
    for entry in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, entry, MANIFEST_NAME)):
            out.append(entry)
    return out


def suggest_branch_name(output_dir, source_name, at_index):
    """First free name of the form <source>_from4, then _from4_002..."""
    base = "%s_from%d" % (validate_name(source_name), int(at_index))
    root = projects_root(output_dir)
    if not os.path.exists(os.path.join(root, base)):
        return base
    n = 2
    while os.path.exists(os.path.join(root, "%s_%03d" % (base, n))):
        n += 1
    return "%s_%03d" % (base, n)


def branch_project(output_dir, source_name, at_index, new_name,
                   at_take=None):
    """Fork a chain at an approved clip into a brand new project.

    Clips 1..at_index are carried over as approved, so the branch's next
    render is clip at_index+1, conditioned on the same latent the source
    used. The source project is not touched at all.

    `at_take` picks which take of the branch-point clip becomes the new
    chain's tail. Any take is legitimate: every take of clip N was
    conditioned on the same clip N-1, so the one that was approved holds
    no special position in the chain -- it was just the one you kept.
    A superseded take is recovered from .trash/ if that is where it went.

    Earlier clips come across at their SELECTED takes; alternates stay
    behind in the source. Files are genuinely COPIED, not linked: a branch has to be
    an independent workspace, so purging, moving, syncing or archiving
    either project can never reach into the other. That costs real disk -
    an AV latent is the expensive half - which is the price of the two
    projects having nothing shared to break.
    """
    src = Project(output_dir, source_name)
    at_index = int(at_index)
    entry = src._entry(at_index)
    if entry["status"] != "approved":
        raise ProjectError(
            "h3_suite: clip %d is %s; branch from an approved clip so the "
            "new chain has a settled tail to continue from."
            % (at_index, entry["status"]))
    carried = [dict(c) for c in src.clips[:at_index]]
    if at_take is not None and int(at_take) != entry["take"]:
        at_take = int(at_take)
        options = src.available_takes(at_index)
        match = [t for t in options if t["take"] == at_take]
        if not match:
            raise ProjectError(
                "h3_suite: clip %d has no take %d with files on disk "
                "(available: %s). A purged take cannot be branched from."
                % (at_index, at_take,
                   ", ".join("%d(%s)" % (t["take"], t["location"])
                             for t in options) or "none"))
        chosen = match[0]
        tip = carried[-1]
        tip["take"] = chosen["take"]
        tip["basename"] = chosen["basename"]
        if chosen.get("meta"):
            tip["meta"] = dict(chosen["meta"])
        else:
            tip.pop("meta", None)
        tip["takes"] = None            # rebuilt below from the choice

    not_approved = [c["index"] for c in carried
                    if c["status"] != "approved"]
    if not_approved:
        raise ProjectError(
            "h3_suite: clips %s before the branch point are not approved."
            % ", ".join(str(i) for i in not_approved))

    new_name = validate_name(new_name)
    dest_root = os.path.join(projects_root(output_dir), new_name)
    if os.path.exists(dest_root):
        raise ProjectError(
            "h3_suite: project %r already exists; pick another name."
            % new_name)

    dest = Project(output_dir, new_name, create=True)
    copied = 0
    total_bytes = 0
    try:
        for c in carried:
            where = src.locate_pair(c["basename"])
            if where is None:
                raise ProjectError(
                    "h3_suite: clip %d take %d (%s) has no files left in "
                    "the source project." % (c["index"], c["take"],
                                             c["basename"]))
            for ext in (VIDEO_EXT, LATENT_EXT, SIDECAR_EXT):
                s_path = os.path.join(where, c["basename"] + ext)
                if not os.path.isfile(s_path):
                    continue                      # sidecar is optional
                d_path = os.path.join(dest.clips_dir,
                                      os.path.basename(s_path))
                shutil.copy2(s_path, d_path)
                copied += 1
                total_bytes += os.path.getsize(d_path)
            dest.clips.append({
                "index": c["index"], "take": c["take"],
                "status": "approved", "basename": c["basename"],
                "from": c.get("from"),
                **({"meta": dict(c["meta"])} if c.get("meta") else {}),
                "takes": [{"take": c["take"], "basename": c["basename"],
                           "meta": c.get("meta")}],
            })
        dest.branched_from = {"project": src.name, "at_index": at_index}
        dest._write()
    except Exception:
        shutil.rmtree(dest_root, ignore_errors=True)  # no half-branches
        raise
    _LOG.info("h3_suite: branched %r at clip %d into %r (%d files, "
              "%.1f MB copied)", src.name, at_index, new_name, copied,
              total_bytes / (1024.0 * 1024.0))
    return dest


class Project:
    """One project folder plus its manifest, loaded eagerly.

    Every mutating method rewrites the manifest atomically before
    returning; there is no separate save() to forget.
    """

    def __init__(self, output_dir, name, create=False):
        self.name = validate_name(name)
        self.root = os.path.realpath(
            os.path.join(projects_root(output_dir), self.name))
        # the resolved root must still be inside the projects root; a
        # symlinked project folder escaping the tree is refused outright
        expected_parent = os.path.realpath(projects_root(output_dir))
        if os.path.dirname(self.root) != expected_parent:
            raise ProjectError(
                "h3_suite: project folder %r resolves outside the projects "
                "root; refusing." % self.root)
        self.manifest_path = os.path.join(self.root, MANIFEST_NAME)
        self.clips_dir = os.path.join(self.root, CLIPS_DIRNAME)
        self.trash_dir = os.path.join(self.root, TRASH_DIRNAME)
        if os.path.isfile(self.manifest_path):
            self._load()
        elif create:
            os.makedirs(self.clips_dir, exist_ok=True)
            self.clips = []
            self.branched_from = None
            self._write()
        else:
            raise ProjectError(
                "h3_suite: project %r does not exist (no %s in %s)."
                % (self.name, MANIFEST_NAME, self.root))

    # -- manifest io -------------------------------------------------------

    def _load(self):
        with open(self.manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != VERSION:
            raise ProjectError(
                "h3_suite: %s has manifest version %r, this build reads "
                "version %d." % (self.manifest_path, data.get("version"),
                                 VERSION))
        self.clips = list(data.get("clips", []))
        self.branched_from = data.get("branched_from")
        self._check_invariants()

    def _write(self):
        self._check_invariants()
        data = {"version": VERSION, "name": self.name, "clips": self.clips}
        if getattr(self, "branched_from", None):
            data["branched_from"] = self.branched_from
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".manifest_",
                                   suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self.manifest_path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _check_invariants(self):
        pend = [c for c in self.clips if c["status"] == "pending"]
        if len(pend) > 1:
            raise ProjectError("h3_suite: manifest has %d pending clips; "
                               "the invariant is at most one." % len(pend))
        if pend and self.clips[-1]["status"] != "pending":
            raise ProjectError("h3_suite: a pending clip is not the last "
                               "entry; the manifest is corrupt.")
        for pos, c in enumerate(self.clips, start=1):
            if c["index"] != pos:
                raise ProjectError(
                    "h3_suite: clip at position %d carries index %d; "
                    "position and index must agree." % (pos, c["index"]))
            if not _BASENAME_RE.match(c["basename"]):
                raise ProjectError("h3_suite: clip basename %r does not "
                                   "match clip_NNN_takeN." % c["basename"])

    # -- state queries -----------------------------------------------------

    def mtime_token(self):
        """Cache key for IS_CHANGED: manifest identity + mtime."""
        try:
            st = os.stat(self.manifest_path)
            return "%s:%d" % (self.manifest_path, st.st_mtime_ns)
        except OSError:
            return float("NaN")

    def pending(self):
        if self.clips and self.clips[-1]["status"] == "pending":
            return self.clips[-1]
        return None

    def approved(self):
        return [c for c in self.clips if c["status"] == "approved"]

    def chain_tail(self):
        """The approved clip the NEXT render continues from, or None."""
        appr = self.approved()
        return appr[-1] if appr else None

    def chain_active(self):
        return self.chain_tail() is not None

    def _paths(self, basename):
        return (os.path.join(self.clips_dir, basename + VIDEO_EXT),
                os.path.join(self.clips_dir, basename + LATENT_EXT),
                os.path.join(self.clips_dir, basename + SIDECAR_EXT))

    def tail_latent_path(self):
        """The latent to condition the next clip on, existence-checked."""
        tail = self.chain_tail()
        if tail is None:
            return None
        video, lat, _side = self._paths(tail["basename"])
        if not os.path.isfile(lat):
            raise ProjectError(
                "h3_suite: approved clip %d's latent is missing (%s). The "
                "chain cannot continue from a clip whose latent is gone."
                % (tail["index"], lat))
        return lat

    def clip_video_path(self, basename):
        return self._paths(basename)[0]

    def clip_sidecar_path(self, basename):
        return self._paths(basename)[2]

    def next_save(self):
        """(index, take, basename) the next render should write.

        A pending clip means the next run is a RE-ROLL of it: same index,
        next take. No pending clip means a fresh next index, take 1.
        """
        pend = self.pending()
        if pend is not None:
            index, take = pend["index"], pend["take"] + 1
        else:
            index, take = len(self.clips) + 1, 1
        return index, take, "clip_%03d_take%d" % (index, take)

    # -- transitions -------------------------------------------------------

    def record_render(self, index, take, meta=None):
        """A render finished and its pair is on disk: make it the pending
        clip. A previous pending take of the same index is superseded and
        its pair is trashed (the take number preserves it in .trash/).

        `meta` is a small dict of render facts (dimensions, frame count,
        duration, seed) stored in the manifest so the panel and any later
        reuse can read them without probing the files.
        """
        basename = "clip_%03d_take%d" % (index, take)
        video, lat, _side = self._paths(basename)
        for p in (video, lat):
            if not os.path.isfile(p):
                raise ProjectError(
                    "h3_suite: record_render(%d, %d) but %s is missing; "
                    "the pair must be written before it is recorded."
                    % (index, take, p))
        pend = self.pending()
        prior_takes = []
        if pend is not None:
            if pend["index"] != index:
                raise ProjectError(
                    "h3_suite: rendered clip %d while clip %d is pending; "
                    "approve or reject the pending clip first."
                    % (index, pend["index"]))
            # keep the superseded take on disk so it can be compared and
            # re-selected; disk cost is bounded by discard_other_takes()
            prior_takes = list(pend.get("takes") or [])
            if not any(t["take"] == pend["take"] for t in prior_takes):
                prior_takes.append({"take": pend["take"],
                                    "basename": pend["basename"],
                                    "meta": pend.get("meta")})
            self.clips.pop()
        elif index != len(self.clips) + 1:
            raise ProjectError(
                "h3_suite: rendered clip %d but the chain is at %d clips; "
                "the next index must be %d."
                % (index, len(self.clips), len(self.clips) + 1))
        tail = self.chain_tail()
        entry = {
            "index": index, "take": take, "status": "pending",
            "basename": basename,
            "from": tail["basename"] if tail else None,
        }
        if meta:
            entry["meta"] = dict(meta)
        takes = [t for t in prior_takes if t["take"] != take]
        takes.append({"take": take, "basename": basename, "meta": meta})
        entry["takes"] = sorted(takes, key=lambda t: t["take"])
        self.clips.append(entry)
        self._write()
        return basename

    def locate_pair(self, basename):
        """Where a take's files actually are: clips/, .trash/, or gone.

        A superseded or rejected take keeps its name; only its folder
        changes. Branching wants to reach those, so resolution has to
        cover both without ever leaving the project.
        """
        if not _BASENAME_RE.match(basename):
            raise ProjectError("h3_suite: %r is not a clip basename."
                               % basename)
        for folder in (self.clips_dir, self.trash_dir):
            video = os.path.join(folder, basename + VIDEO_EXT)
            lat = os.path.join(folder, basename + LATENT_EXT)
            if os.path.isfile(video) and os.path.isfile(lat):
                return folder
        return None

    def available_takes(self, index):
        """Every take of a clip that still has files, with its location."""
        out = []
        for t in self.takes_of(index):
            where = self.locate_pair(t["basename"])
            if where is None:
                continue
            out.append({
                "take": t["take"], "basename": t["basename"],
                "meta": t.get("meta"),
                "location": ("trash" if where == self.trash_dir
                             else "clips"),
            })
        return sorted(out, key=lambda t: t["take"])

    def takes_of(self, index):
        entry = self._entry(index)
        return list(entry.get("takes")
                    or [{"take": entry["take"],
                         "basename": entry["basename"],
                         "meta": entry.get("meta")}])

    def select_take(self, index, take):
        """Point a pending clip at one of its other takes.

        Only the pending clip can switch: an approved clip is what later
        clips were conditioned on, so changing it silently would break
        the chain. Reopen it first, which states the cascade.
        """
        entry = self._entry(index)
        if entry["status"] != "pending":
            raise ProjectError(
                "h3_suite: clip %d is %s; only a pending clip can switch "
                "takes. Reopen it first." % (index, entry["status"]))
        take = int(take)
        match = [t for t in self.takes_of(index) if t["take"] == take]
        if not match:
            raise ProjectError(
                "h3_suite: clip %d has no take %d (available: %s)."
                % (index, take,
                   ", ".join(str(t["take"]) for t in self.takes_of(index))))
        chosen = match[0]
        where = self.locate_pair(chosen["basename"])
        if where is None:
            raise ProjectError(
                "h3_suite: take %d's files are gone; it was purged and "
                "cannot be selected." % take)
        if where == self.trash_dir:
            raise ProjectError(
                "h3_suite: take %d is in .trash/. Branch from it to build "
                "on it, or restore it by hand into clips/." % take)
        entry["take"] = take
        entry["basename"] = chosen["basename"]
        if chosen.get("meta"):
            entry["meta"] = dict(chosen["meta"])
        self._write()
        return entry

    def discard_other_takes(self, index):
        """Trash every take of a clip except the selected one."""
        entry = self._entry(index)
        keep = entry["basename"]
        dropped = []
        takes = []
        for t in self.takes_of(index):
            rec = dict(t)
            if t["basename"] != keep:
                self._trash_pair(t["basename"])
                dropped.append(t["basename"])
                # remember it: the files still exist in .trash/ until a
                # purge, and a later branch may want one of them as its
                # tail. Forgetting here would strand recoverable takes.
                rec["trashed"] = True
            else:
                rec.pop("trashed", None)
            takes.append(rec)
        entry["takes"] = takes
        self._write()
        return dropped

    def approve(self):
        pend = self.pending()
        if pend is None:
            raise ProjectError("h3_suite: nothing is pending to approve.")
        pend["status"] = "approved"
        self._write()
        return pend

    def reject(self):
        """Drop the pending clip entirely; its pair goes to .trash/."""
        pend = self.pending()
        if pend is None:
            raise ProjectError("h3_suite: nothing is pending to reject.")
        for t in self.takes_of(pend["index"]):
            self._trash_pair(t["basename"])
        self.clips.pop()
        self._write()
        return pend

    def reopen(self, index):
        """Set an approved clip back to pending; everything after it was
        conditioned on content about to change, so it is dropped to trash.
        Returns the basenames trashed, so a UI can confirm the blast
        radius BEFORE calling (see cascade_of)."""
        pend = self.pending()
        if pend is not None:
            raise ProjectError(
                "h3_suite: clip %d is pending; approve or reject it before "
                "reopening an earlier clip." % pend["index"])
        target = self._entry(index)
        if target["status"] != "approved":
            raise ProjectError("h3_suite: clip %d is %s, not approved."
                               % (index, target["status"]))
        dropped = [c["basename"] for c in self.clips[index:]]
        for c in self.clips[index:]:
            for t in self.takes_of(c["index"]):
                self._trash_pair(t["basename"])
        self.clips = self.clips[:index]
        target["status"] = "pending"
        self._write()
        return dropped

    def cascade_of(self, index):
        """What reopen(index) would drop, without doing it."""
        self._entry(index)
        return [c["basename"] for c in self.clips[index:]]

    def purge_trash(self):
        if os.path.isdir(self.trash_dir):
            shutil.rmtree(self.trash_dir)
        return True

    # -- internals ---------------------------------------------------------

    def _entry(self, index):
        if not 1 <= int(index) <= len(self.clips):
            raise ProjectError("h3_suite: no clip %s in a %d-clip project."
                               % (index, len(self.clips)))
        return self.clips[int(index) - 1]

    def _contained(self, path):
        """realpath containment: path must live inside clips_dir."""
        real = os.path.realpath(path)
        root = os.path.realpath(self.clips_dir)
        return os.path.commonpath([real, root]) == root

    def _trash_pair(self, basename):
        """Move a clip pair to .trash/, never off-project, never unlink.

        Both checks, always: the basename must match the clip pattern
        (so a crafted manifest cannot name project.json or ../anything),
        and every resolved path must live inside clips_dir.
        """
        if not _BASENAME_RE.match(basename):
            raise ProjectError(
                "h3_suite: refusing to trash %r; not a clip basename."
                % basename)
        os.makedirs(self.trash_dir, exist_ok=True)
        for src in self._paths(basename):
            if not self._contained(src):
                raise ProjectError(
                    "h3_suite: %r resolves outside the project's clips "
                    "folder; refusing to touch it." % src)
            if os.path.isfile(src):
                dst = os.path.join(self.trash_dir, os.path.basename(src))
                if os.path.exists(dst):  # same basename trashed before
                    stem, ext = os.path.splitext(dst)
                    k = 2
                    while os.path.exists("%s.%d%s" % (stem, k, ext)):
                        k += 1
                    dst = "%s.%d%s" % (stem, k, ext)
                os.replace(src, dst)
                _LOG.info("h3_suite: trashed %s", src)
