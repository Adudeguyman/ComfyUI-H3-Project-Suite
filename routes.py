"""HTTP routes for the project layer.

The Hub node RESOLVES state; changing it (approve, reject, reopen, purge)
happens here, out of band of graph execution, so a review click never needs
a queue press. The panel UI will call these; until it exists they work from
curl. Every route re-reads the manifest fresh -- the node side notices via
IS_CHANGED on the next queue press.

Registered only when ComfyUI's PromptServer is importable; headless tests
import this module without it and get a no-op.
"""

import logging
import os

_LOG = logging.getLogger("h3_suite")

try:
    from aiohttp import web
    from server import PromptServer  # ComfyUI's own server module
    _server = PromptServer.instance
except Exception:  # headless / tests / very old ComfyUI
    web = None
    _server = None


def _register():
    import folder_paths
    from .project import Project, ProjectError, list_projects

    routes = _server.routes

    def _project(request, create=False):
        name = request.rel_url.query.get("name") or request.get("name")
        return Project(folder_paths.get_output_directory(), name,
                       create=create)

    def _state(p):
        index, take, basename = p.next_save()
        return {
            "name": p.name,
            "auto_approve": bool(getattr(p, "auto_approve", False)),
            "clips": p.clips,
            "chain_active": p.chain_active(),
            "pending": p.pending(),
            "next_save": {"index": index, "take": take,
                          "basename": basename},
        }

    def _json_post(handler):
        async def wrapped(request):
            try:
                body = await request.json()
            except Exception:
                body = {}
            try:
                return web.json_response(handler(body))
            except ProjectError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except Exception as exc:  # keep the panel debuggable
                _LOG.exception("h3_suite route failed")
                return web.json_response({"error": str(exc)}, status=500)
        return wrapped

    @routes.get("/h3_suite/projects")
    async def projects(request):
        return web.json_response(
            {"projects": list_projects(folder_paths.get_output_directory())})

    @routes.get("/h3_suite/project/state")
    async def state(request):
        try:
            p = _project(request)
            return web.json_response(_state(p))
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)

    @routes.post("/h3_suite/project/create")
    @_json_post
    def create(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"), create=True)
        return _state(p)

    @routes.post("/h3_suite/project/approve")
    @_json_post
    def approve(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        p.approve()
        return _state(p)

    @routes.post("/h3_suite/project/reject")
    @_json_post
    def reject(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        p.reject()
        return _state(p)

    @routes.post("/h3_suite/project/reopen")
    @_json_post
    def reopen(body):
        from .project import snapshot_project, suggest_snapshot_name
        import folder_paths as fp
        out_dir = fp.get_output_directory()
        p = Project(out_dir, body.get("name"))
        index = int(body.get("index", 0))
        if not body.get("confirm"):
            # first call: report the blast radius and what a backup would
            # be called, change nothing
            return {"would_drop": p.cascade_of(index),
                    "snapshot_name": suggest_snapshot_name(
                        out_dir, p.name)}
        snapshot = None
        if body.get("snapshot"):
            # snapshot BEFORE reopening, so a failure here leaves the
            # chain intact rather than half-dismantled
            snap = snapshot_project(out_dir, p.name,
                                    body.get("snapshot_name") or None)
            snapshot = snap.name
            p = Project(out_dir, body.get("name"))   # re-read after copy
        dropped = p.reopen(index)
        out = _state(p)
        out["dropped"] = dropped
        out["snapshot"] = snapshot
        return out

    @routes.get("/h3_suite/project/storage")
    async def storage(request):
        try:
            p = _project(request)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        out = p.storage_report()
        out["cleanup"] = p.cleanup_takes(dry_run=True)
        return web.json_response(out)

    @routes.post("/h3_suite/project/cleanup_takes")
    @_json_post
    def cleanup_takes(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        result = p.cleanup_takes()
        out = _state(p)
        out["cleaned"] = len(result["planned"])
        out["bytes"] = result["bytes"]
        out["storage"] = p.storage_report()
        return out

    @routes.post("/h3_suite/project/level_match")
    @_json_post
    def level_match(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        p.set_level_match(int(body.get("index")),
                          bool(body.get("enabled")))
        return _state(p)

    @routes.get("/h3_suite/project/level_match_preview")
    async def level_match_preview(request):
        """What the correction WOULD do, measured, without writing."""
        try:
            p = _project(request)
            index = int(request.rel_url.query.get("index", 0))
            clips = p.clips
            if index <= 1 or index > len(clips):
                raise ProjectError("h3_suite: no join before clip %d."
                                   % index)
            from .level_match import measure
            prev, cur = clips[index - 2], clips[index - 1]
            plan = measure(p.clip_video_path(prev["basename"]),
                           p.clip_video_path(cur["basename"]))
            # if the PREVIOUS join is corrected too, this measurement is
            # only exact while that correction has faded out before its
            # clip's tail - which is the level this join measures against
            chained = None
            if prev.get("level_match") and index > 2:
                pprev = clips[index - 3]
                try:
                    up = measure(p.clip_video_path(pprev["basename"]),
                                 p.clip_video_path(prev["basename"]))
                    if up is not None and up.get("reaches_tail"):
                        chained = ("clip %d's own correction is still "
                                   "active at its tail, so this join will "
                                   "be measured against the corrected "
                                   "level at export, not the figure shown "
                                   "here" % prev["index"])
                except Exception:
                    pass
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        if plan is None:
            return web.json_response(
                {"needed": False,
                 "message": "this join is already level; nothing to "
                            "correct"})
        return web.json_response({
            "needed": True, "step": round(plan["step"], 2),
            "tau": (round(plan["tau"], 1) if plan["tau"] else None),
            "span": plan["span"],
            "gain": round(plan["gain"], 4),
            "reaches_tail": plan.get("reaches_tail", False),
            "chained_note": chained,
        })

    @routes.post("/h3_suite/project/auto_approve")
    @_json_post
    def auto_approve(body):
        """Turn this project's review gate off, or back on."""
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        on = p.set_auto_approve(bool(body.get("on")))
        _LOG.warning(
            "h3_suite: project %r auto-approve %s", p.name,
            "ON - the chain will progress without review" if on else "off")
        return _state(p)

    @routes.get("/h3_suite/project/drift")
    async def drift(request):
        """Per-clip picture statistics across the approved chain."""
        try:
            p = _project(request)
            clips = p.clips
            if len(clips) < 3:
                return web.json_response(
                    {"error": "needs at least 3 approved clips to show a "
                              "trend"}, status=400)
            from .chain_report import measure_chain
            paths = [p.clip_video_path(c["basename"]) for c in clips]
            labels = ["clip %d take %d" % (c["index"], c.get("take", 1))
                      for c in clips]
            out = measure_chain(paths, labels)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(out)

    @routes.post("/h3_suite/project/purge_trash")
    @_json_post
    def purge(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        before = p.storage_report()["trash_bytes"]
        p.purge_trash()
        out = _state(p)
        out["freed_bytes"] = before
        out["storage"] = p.storage_report()
        return out

    def _safe_export_name(raw, fallback):
        """A plain .mp4 filename inside the project root, never a path."""
        import re as _re
        name = os.path.basename((raw or "").strip())
        name = _re.sub(r"[^A-Za-z0-9 ._-]", "", name)
        if name.lower().endswith(".mp4"):
            name = name[:-4]
        name = name.strip(" .")
        if not name:
            name = fallback
        return name + ".mp4"

    def _suggest_export(p, preview):
        """First free name: base.mp4, then base_002.mp4, base_003.mp4..."""
        base = "%s_%s" % (p.name, "preview" if preview else "master")
        if not os.path.exists(os.path.join(p.root, base + ".mp4")):
            return base + ".mp4"
        n = 2
        while os.path.exists(os.path.join(p.root, "%s_%03d.mp4" % (base, n))):
            n += 1
        return "%s_%03d.mp4" % (base, n)

    @routes.get("/h3_suite/project/export_name")
    async def export_name(request):
        try:
            p = _project(request)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        preview = request.rel_url.query.get("preview") in ("1", "true")
        return web.json_response({"suggested": _suggest_export(p, preview)})

    @routes.post("/h3_suite/project/select_take")
    @_json_post
    def select_take(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        p.select_take(int(body.get("index")), int(body.get("take")))
        return _state(p)

    @routes.post("/h3_suite/project/discard_takes")
    @_json_post
    def discard_takes(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        dropped = p.discard_other_takes(int(body.get("index")))
        out = _state(p)
        out["dropped"] = dropped
        return out

    @routes.get("/h3_suite/project/branch_name")
    async def branch_name(request):
        import folder_paths as fp
        from .project import suggest_branch_name
        name = request.rel_url.query.get("name")
        index = int(request.rel_url.query.get("index", 1))
        try:
            p = Project(fp.get_output_directory(), name)
            entry = p._entry(index)
            return web.json_response({
                "suggested": suggest_branch_name(
                    fp.get_output_directory(), name, index),
                "takes": p.available_takes(index),
                "current_take": entry["take"],
            })
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post("/h3_suite/project/branch")
    @_json_post
    def branch(body):
        import folder_paths as fp
        from .project import branch_project
        take = body.get("take")
        dest = branch_project(fp.get_output_directory(), body.get("name"),
                              int(body.get("index")), body.get("new_name"),
                              at_take=None if take in (None, "")
                              else int(take))
        out = _state(dest)
        out["branched_from"] = dest.branched_from
        return out

    @routes.post("/h3_suite/project/open_folder")
    @_json_post
    def open_folder(body):
        import subprocess
        import sys
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        target = os.path.realpath(p.root)
        if not os.path.isdir(target):
            raise ProjectError("h3_suite: %s does not exist." % target)
        # this opens on the machine RUNNING ComfyUI, not the one running
        # the browser - obvious locally, surprising over --listen, so the
        # response carries the path for the panel to report either way
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
            elif os.name == "nt":
                os.startfile(target)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", target],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except FileNotFoundError:
            raise ProjectError(
                "h3_suite: no file manager opener available on the server "
                "(%s). The folder is at %s" % (sys.platform, target))
        return {"path": target}

    @routes.post("/h3_suite/project/export")
    @_json_post
    def export(body):
        import shutil
        import subprocess
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        clips = list(p.approved())
        # a pending clip can be appended for a seamless preview of the
        # join you are about to judge; the file is named _preview so it
        # can never be mistaken for the deliverable master
        preview = bool(body.get("include_pending")) and p.pending()
        if preview:
            clips.append(p.pending())
        if not clips:
            raise ProjectError("h3_suite: nothing to export.")
        if body.get("use_latents"):
            from .export_latents import export_from_latents
            default = _suggest_export(p, preview)[:-4]
            fname = _safe_export_name(body.get("filename"), default)
            master = os.path.join(p.root, fname)
            real = os.path.realpath(master)
            if os.path.dirname(real) != os.path.realpath(p.root):
                raise ProjectError(
                    "h3_suite: export filename must stay in the project "
                    "folder.")
            try:
                info = export_from_latents(
                    p, clips, master,
                    level_match=bool(body.get("level_match", True)))
            except RuntimeError as exc:
                raise ProjectError(str(exc))
            return {"exported": fname, "from_latents": True,
                    "level_matched": info["level_matched"],
                    "preview": bool(preview)}
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProjectError("h3_suite: ffmpeg not found on PATH; "
                               "install it to export a master.")
        missing = [c["basename"] for c in clips
                   if not os.path.isfile(p.clip_video_path(c["basename"]))]
        if missing:
            raise ProjectError("h3_suite: clip videos missing: %s"
                               % ", ".join(missing))
        # level matching: correct flagged clips into temp files first, so
        # the concat itself stays a plain join of ready files
        tmp_dir = None
        matched = []
        paths = [p.clip_video_path(c["basename"]) for c in clips]
        if body.get("level_match", True):
            flagged = [i for i, c in enumerate(clips)
                       if i > 0 and c.get("level_match")]
            if flagged:
                from .level_match import correct
                tmp_dir = os.path.join(p.root, ".levelmatch")
                os.makedirs(tmp_dir, exist_ok=True)
                for i in flagged:
                    dst = os.path.join(tmp_dir,
                                       clips[i]["basename"] + ".mp4")
                    try:
                        plan = correct(paths[i - 1], paths[i], dst)
                    except Exception as exc:
                        _LOG.warning("h3_suite: level match failed on %s: "
                                     "%s", clips[i]["basename"], exc)
                        continue
                    if plan is not None:
                        paths[i] = dst
                        matched.append(clips[i]["index"])
        list_path = os.path.join(p.root, ".concat.txt")
        with open(list_path, "w", encoding="utf-8") as fh:
            for path in paths:
                fh.write("file '%s'\n" % path.replace("'", "'\\''"))
        # no explicit name means the first FREE name, never a silent
        # overwrite of a master someone already kept
        default = _suggest_export(p, preview)[:-4]
        fname = _safe_export_name(body.get("filename"), default)
        master = os.path.join(p.root, fname)
        real = os.path.realpath(master)
        if os.path.dirname(real) != os.path.realpath(p.root):
            raise ProjectError(
                "h3_suite: export filename must stay in the project "
                "folder.")
        # untouched clips are identical by construction and stream copy;
        # once any clip has been re-encoded for level matching the whole
        # concat has to be re-encoded so the parameters agree
        if matched:
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i",
                   list_path, "-c:v", "libx264", "-crf", "17",
                   "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "-movflags", "+faststart", master]
        else:
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i",
                   list_path, "-c", "copy", master]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        os.unlink(list_path)
        if tmp_dir and os.path.isdir(tmp_dir):
            import shutil as _sh
            _sh.rmtree(tmp_dir, ignore_errors=True)
        if proc.returncode != 0:
            raise ProjectError("h3_suite: ffmpeg concat failed: %s"
                               % proc.stderr[-400:])
        out = _state(p)
        out["master"] = master
        out["preview"] = bool(preview)
        out["clip_count"] = len(clips)
        out["level_matched"] = matched
        return out

    def _input_root():
        import folder_paths as fp
        getter = getattr(fp, "get_input_directory", None)
        if getter is None:
            raise ProjectError(
                "h3_suite: this ComfyUI does not expose an input folder, "
                "so importing cannot find your videos.")
        return os.path.realpath(getter())

    def _safe_source(rel):
        """A path inside ComfyUI's input folder, or nothing."""
        root = _input_root()
        real = os.path.realpath(os.path.join(root, rel or ""))
        if os.path.commonpath([real, root]) != root:
            raise ProjectError("h3_suite: that file is outside ComfyUI's "
                               "input folder.")
        if not os.path.isfile(real):
            raise ProjectError("h3_suite: no such file: %s" % rel)
        return real

    @routes.get("/h3_suite/source/list")
    async def source_list(request):
        """Videos in ComfyUI's input folder, newest first."""
        try:
            root = _input_root()
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        exts = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
        out = []
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(exts):
                    continue
                full = os.path.join(dirpath, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out.append({"rel": os.path.relpath(full, root),
                            "size": st.st_size, "mtime": st.st_mtime})
        out.sort(key=lambda e: e["mtime"], reverse=True)
        return web.json_response({"files": out[:400]})

    @routes.post("/h3_suite/source/upload")
    async def source_upload(request):
        """Take a file from the browser into ComfyUI's input folder.

        Written to a temp name in the destination folder and renamed
        into place, so a half-received upload never appears in the
        picker as a playable file.
        """
        import shutil
        import tempfile

        try:
            root = _input_root()
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        dest_dir = os.path.join(root, "h3_imports")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        exts = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
        try:
            reader = await request.multipart()
        except Exception as exc:
            return web.json_response({"error": "not a file upload (%s)"
                                              % exc}, status=400)
        written = None
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "file" or not part.filename:
                continue
            # the browser's filename is untrusted: keep the basename, and
            # only an extension we are prepared to open
            base = os.path.basename(part.filename).replace("\\", "_")
            base = "".join(ch for ch in base
                           if ch.isalnum() or ch in " ._-()[]").strip()
            stem, ext = os.path.splitext(base)
            if ext.lower() not in exts:
                return web.json_response(
                    {"error": "%s is not a video this can open (%s)"
                              % (part.filename, ", ".join(exts))},
                    status=400)
            stem = stem or "import"
            final = os.path.join(dest_dir, stem + ext)
            n = 1
            while os.path.exists(final):
                final = os.path.join(dest_dir, "%s_%d%s" % (stem, n, ext))
                n += 1
            fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
            size = 0
            try:
                with os.fdopen(fd, "wb") as fh:
                    while True:
                        chunk = await part.read_chunk(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        fh.write(chunk)
                os.replace(tmp, final)
            except Exception as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                return web.json_response({"error": str(exc)}, status=500)
            written = {"rel": os.path.relpath(final, root), "size": size}
            break
        if written is None:
            return web.json_response({"error": "no file in the upload"},
                                     status=400)
        _LOG.info("h3_suite: uploaded %s (%.1f MB) for import",
                  written["rel"], written["size"] / 1048576.0)
        return web.json_response(written)

    @routes.get("/h3_suite/source/probe")
    async def source_probe(request):
        """Real frame count and rate, read from the container."""
        try:
            real = _safe_source(request.rel_url.query.get("rel"))
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        try:
            from .import_source import probe_video
            info = probe_video(real)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(info)

    @routes.get("/h3_suite/source/file")
    async def source_file(request):
        try:
            real = _safe_source(request.rel_url.query.get("rel"))
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.FileResponse(real)

    @routes.post("/h3_suite/source/import")
    @_json_post
    def source_import(body):
        """Decode the chosen window, encode it, write it as a clip."""
        import folder_paths as fp
        from .import_source import import_window
        p = Project(fp.get_output_directory(), body.get("name"))
        real = _safe_source(body.get("rel"))
        try:
            info = import_window(
                p, real,
                start=int(body.get("start") or 0),
                frames=int(body.get("frames") or 0),
                width=int(body.get("width") or 0),
                height=int(body.get("height") or 0),
                crop=body.get("crop") or "center",
                with_audio=bool(body.get("with_audio", True)))
        except RuntimeError as exc:
            raise ProjectError(str(exc))
        out = _state(p)
        out["imported"] = info
        return out

    @routes.get("/h3_suite/project/video")
    async def video(request):
        try:
            p = _project(request)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        basename = request.rel_url.query.get("basename", "")
        # a take may live in clips/ or, once superseded, in .trash/ - the
        # branch preview has to be able to watch either
        try:
            where = p.locate_pair(basename)
        except ProjectError:
            return web.json_response({"error": "no such clip"}, status=404)
        if where is None:
            return web.json_response({"error": "no such clip"}, status=404)
        real = os.path.realpath(os.path.join(where, basename + ".mp4"))
        root = os.path.realpath(p.root)
        if os.path.commonpath([real, root]) != root or not os.path.isfile(
                real):
            return web.json_response({"error": "no such clip"}, status=404)
        return web.FileResponse(real)

    _LOG.info("h3_suite: project routes registered under /h3_suite/")


if _server is not None and web is not None:
    _register()
