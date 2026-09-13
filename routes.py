"""HTTP routes for the project layer.

The Hub node RESOLVES state; changing it (approve, reject, reopen, purge)
happens here, out of band of graph execution, so a review click never needs
a queue press. Every route re-reads the manifest fresh -- the node side
notices via IS_CHANGED on the next queue press.

Who may change state. ComfyUI has no login, so every POST here is guarded
three ways before its handler runs (see SECURITY.md):

  1. the request must not be cross-site by the browser's own account
     (Sec-Fetch-Site / Origin against Host);
  2. it must carry the session token in the X-H3Suite-Token header. The
     token is minted once per server process and handed out only by a
     same-origin GET, which a page on another origin can send but can
     never read;
  3. a JSON route must say Content-Type: application/json, so a cross-
     origin page cannot reach it without a CORS preflight the server
     never approves.

From a shell the same sequence is: GET /h3_suite/token, then POST with
that header. A GET never changes anything on disk.

Registered only when ComfyUI's PromptServer is importable; headless tests
import this module without it and get a no-op.
"""

import hmac
import logging
import os
import secrets

_LOG = logging.getLogger("h3_suite")

# One random token per server process. Every state-changing route demands
# it in a request header; the panel fetches it from a same-origin GET.
_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-H3Suite-Token"

# the only files the import side will list, probe, serve or decode
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


def _host_port(value):
    """('host', port or None) from a Host header or an Origin's authority.

    Hand-parsed rather than via a URL library: the two forms that matter
    are host[:port] and [ipv6]:port, and anything else is not a value a
    browser would send as its own origin, so it parses as no host at all
    and is refused upstream.
    """
    v = (value or "").strip().lower()
    if "@" in v:                              # no browser sends userinfo
        return "", None
    if v.startswith("["):                     # [ipv6] or [ipv6]:port
        end = v.find("]")
        if end < 0:
            return "", None
        host, rest = v[1:end], v[end + 1:]
    else:
        host, sep, port = v.partition(":")
        rest = (":" + port) if sep else ""
    if not host:
        return "", None
    if not rest:
        return host, None
    if not rest.startswith(":") or not rest[1:].isdigit():
        return "", None
    return host, int(rest[1:])


def _authority(origin):
    """host[:port] out of an Origin such as https://h:1 - nothing else."""
    _scheme, sep, rest = origin.strip().partition("://")
    return rest.split("/", 1)[0] if sep else ""


def is_cross_site(headers):
    """True when the browser says this request came from another origin.

    Sec-Fetch-Site is authoritative when present: only same-origin and
    user-initiated (none) requests pass. Without it, an Origin header must
    name the same host as Host, tolerating one side carrying a default
    port the other omits. A request with neither header (a shell tool)
    is not cross-site; the token check is what stands between it and a
    side effect.
    """
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return True
    origin = (headers.get("Origin") or "").strip()
    if not origin:
        return False
    if origin.lower() == "null":
        return True
    o_host, o_port = _host_port(_authority(origin))
    h_host, h_port = _host_port(headers.get("Host") or "")
    if not o_host or not h_host or o_host != h_host:
        return True
    return not (o_port == h_port or o_port is None or h_port is None)


def token_ok(headers):
    sent = (headers.get(TOKEN_HEADER) or "").encode("utf-8", "replace")
    return hmac.compare_digest(sent, _TOKEN.encode("utf-8"))

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
        res = p.resolution()
        return {
            "name": p.name,
            "auto_approve": bool(getattr(p, "auto_approve", False)),
            "resolution": ({"width": res[0], "height": res[1]}
                           if res else None),
            "clips": p.clips,
            "chain_active": p.chain_active(),
            "pending": p.pending(),
            "next_save": {"index": index, "take": take,
                          "basename": basename},
        }

    def _guard(request, json_only=True):
        """None when the request may change state, else the refusal.

        Order matters for what a probe learns: cross-site is refused
        before the token is even looked at, so a foreign page holding a
        stolen token still gets nothing.
        """
        if is_cross_site(request.headers):
            return web.json_response(
                {"error": "cross-site request refused"}, status=403)
        if not token_ok(request.headers):
            return web.json_response(
                {"error": "missing or stale session token",
                 "token_required": True}, status=403)
        if json_only and request.content_type != "application/json":
            return web.json_response(
                {"error": "expected Content-Type: application/json"},
                status=415)
        return None

    def _json_post(handler):
        async def wrapped(request):
            denied = _guard(request)
            if denied is not None:
                return denied
            try:
                body = await request.json()
            except Exception:
                body = {}
            try:
                return web.json_response(handler(body))
            except ProjectError as exc:
                # the panel shows this as a toast that fades; log it too,
                # or a refused import looks like one that did nothing
                _LOG.warning("h3_suite: %s refused: %s",
                             request.path, exc)
                return web.json_response({"error": str(exc)}, status=400)
            except Exception as exc:  # keep the panel debuggable
                _LOG.exception("h3_suite route failed")
                return web.json_response({"error": str(exc)}, status=500)
        return wrapped

    @routes.get("/h3_suite/token")
    async def token(request):
        """The session token, to same-origin callers only.

        The cross-site check on a GET is what keeps the token private
        when ComfyUI runs with --enable-cors-header: a permissive CORS
        policy would otherwise let another origin read this response.
        """
        if is_cross_site(request.headers):
            return web.json_response(
                {"error": "cross-site request refused"}, status=403)
        return web.json_response({"token": _TOKEN},
                                 headers={"Cache-Control": "no-store"})

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

    @routes.get("/h3_suite/project/folder")
    async def folder(request):
        """Where the project lives on the machine running ComfyUI.

        The panel shows this so the user can open it themselves. The
        server does not launch a file manager: that would be a program
        started by a web request, and it would open on the wrong machine
        whenever ComfyUI runs over --listen anyway.
        """
        try:
            p = _project(request)
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        target = os.path.realpath(p.root)
        if not os.path.isdir(target):
            return web.json_response(
                {"error": "h3_suite: %s does not exist." % target},
                status=404)
        return web.json_response({"path": target})

    @routes.post("/h3_suite/project/export")
    @_json_post
    def export(body):
        import shutil
        import folder_paths as fp
        from .concat import concat_copy, concat_reencode
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
        from .project import check_uniform_size
        check_uniform_size(clips)
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
                    level_match=bool(body.get("level_match", True)),
                    crf=int(body.get("crf") or 16),
                    preset=str(body.get("preset") or "medium"),
                    vae_names=body.get("vae_names") or None)
            except RuntimeError as exc:
                raise ProjectError(str(exc))
            return {"exported": fname, "from_latents": True,
                    "level_matched": info["level_matched"],
                    "preview": bool(preview)}
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
        # join has to be re-encoded so the parameters agree
        try:
            if matched:
                concat_reencode(paths, master, crf=17, preset="medium")
            else:
                concat_copy(paths, master)
        except Exception as exc:
            try:
                os.unlink(master)         # never leave a half-written master
            except OSError:
                pass
            raise ProjectError("h3_suite: joining the clips failed: %s"
                               % exc)
        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
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
        """A video inside ComfyUI's input folder, or nothing.

        Both halves matter: the folder bounds where a read can land, and
        the extension bounds what kind of file this side will ever open
        or serve - the same list the picker shows and the upload accepts.
        """
        root = _input_root()
        real = os.path.realpath(os.path.join(root, rel or ""))
        if os.path.commonpath([real, root]) != root:
            raise ProjectError("h3_suite: that file is outside ComfyUI's "
                               "input folder.")
        if not real.lower().endswith(_VIDEO_EXTS):
            raise ProjectError("h3_suite: %s is not a video this can open "
                               "(%s)." % (rel, ", ".join(_VIDEO_EXTS)))
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
        exts = _VIDEO_EXTS
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
        import tempfile

        # multipart is a CORS-simple content type, so this is the route a
        # cross-origin page could reach with no preflight at all: the
        # origin check and the token are what stop it. The JSON check
        # cannot apply here and is skipped on purpose.
        denied = _guard(request, json_only=False)
        if denied is not None:
            return denied
        try:
            root = _input_root()
        except ProjectError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        dest_dir = os.path.join(root, "h3_imports")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        exts = _VIDEO_EXTS
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
                fit=("fit" if body.get("fit") == "fit" else "fill"),
                crop_offset=float(body.get("crop_offset", 0.5)),
                with_audio=bool(body.get("with_audio", True)),
                vae_names=body.get("vae_names") or None)
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
