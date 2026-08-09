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
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        index = int(body.get("index", 0))
        if not body.get("confirm"):
            # first call: report the blast radius, change nothing
            return {"would_drop": p.cascade_of(index)}
        dropped = p.reopen(index)
        out = _state(p)
        out["dropped"] = dropped
        return out

    @routes.post("/h3_suite/project/purge_trash")
    @_json_post
    def purge(body):
        import folder_paths as fp
        p = Project(fp.get_output_directory(), body.get("name"))
        p.purge_trash()
        return _state(p)

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
            return web.json_response({"suggested": suggest_branch_name(
                fp.get_output_directory(), name, index)})
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post("/h3_suite/project/branch")
    @_json_post
    def branch(body):
        import folder_paths as fp
        from .project import branch_project
        dest = branch_project(fp.get_output_directory(), body.get("name"),
                              int(body.get("index")), body.get("new_name"))
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
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProjectError("h3_suite: ffmpeg not found on PATH; "
                               "install it to export a master.")
        missing = [c["basename"] for c in clips
                   if not os.path.isfile(p.clip_video_path(c["basename"]))]
        if missing:
            raise ProjectError("h3_suite: clip videos missing: %s"
                               % ", ".join(missing))
        list_path = os.path.join(p.root, ".concat.txt")
        with open(list_path, "w", encoding="utf-8") as fh:
            for c in clips:
                fh.write("file '%s'\n"
                         % p.clip_video_path(c["basename"]).replace("'",
                                                                    "'\\''"))
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
        # identical-format clips by construction: stream copy, no re-encode
        proc = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", master],
            capture_output=True, text=True)
        os.unlink(list_path)
        if proc.returncode != 0:
            raise ProjectError("h3_suite: ffmpeg concat failed: %s"
                               % proc.stderr[-400:])
        out = _state(p)
        out["master"] = master
        out["preview"] = bool(preview)
        out["clip_count"] = len(clips)
        return out

    @routes.get("/h3_suite/project/video")
    async def video(request):
        try:
            p = _project(request)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        basename = request.rel_url.query.get("basename", "")
        path = p.clip_video_path(basename)
        # containment before serving: the basename came off the wire
        real = os.path.realpath(path)
        root = os.path.realpath(p.clips_dir)
        if os.path.commonpath([real, root]) != root or not os.path.isfile(
                real):
            return web.json_response({"error": "no such clip"}, status=404)
        return web.FileResponse(real)

    _LOG.info("h3_suite: project routes registered under /h3_suite/")


if _server is not None and web is not None:
    _register()
