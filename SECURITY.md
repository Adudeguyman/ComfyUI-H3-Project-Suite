# Security

ComfyUI has no login. Anyone who can reach the server can queue a
workflow through `/prompt`, and any page open in the same browser can
call its routes. This pack therefore treats two things as untrusted by
construction: every HTTP request it receives, and every free-text widget
on its nodes. This document says what each surface may do and what
enforces it.

## Same-origin enforcement on state-changing routes

Every `POST` under `/h3_suite/` passes three checks in `routes.py`
before its handler runs. The shared guard is `_guard()`; the JSON routes
reach it through the `_json_post` decorator and the multipart upload
calls it directly.

1. **Not cross-site.** `is_cross_site()` reads `Sec-Fetch-Site` when the
   browser sends it and admits only `same-origin` and `none`. Without it,
   the `Origin` header must name the same host as `Host`; a default port
   on one side only is tolerated, `Origin: null` is refused. A request
   with neither header (a shell tool) is not treated as cross-site; the
   token below is what stands between it and a side effect.
   Refused with `403`.
2. **Session token.** A random token is minted once per server process
   (`secrets.token_urlsafe(32)`) and must arrive in the
   `X-H3Suite-Token` header, compared with `hmac.compare_digest`. The
   panel obtains it from `GET /h3_suite/token`, which itself refuses
   cross-site callers and answers with `Cache-Control: no-store`. A page
   on another origin can send a `POST` but can never read that `GET`, so
   it can never learn the header value. This is a server-minted CSRF
   token in the usual sense. Refused with `403` and `token_required`.
3. **JSON content type.** JSON routes require
   `Content-Type: application/json`, which makes the request non-simple
   under CORS: a cross-origin page cannot send it without a preflight,
   and the server never approves one. `aiohttp` does not check the
   content type itself, so this is explicit. Refused with `415`.

The multipart upload (`POST /h3_suite/source/upload`) cannot carry the
JSON requirement, which is exactly why it carries the other two; the
`multipart/form-data` type is CORS-simple and would otherwise be
reachable from any page with no preflight at all.

No auth, no login and no cookie is involved: the token is the
authentication for a side effect, and it lives only in the memory of
the server process and the panel that fetched it.

### Note on ComfyUI core

ComfyUI applies its own `create_origin_only_middleware` to every route
by default, which rejects a request whose `Origin` host differs from
`Host`. That middleware is bypassed when ComfyUI runs with
`--enable-cors-header`, and it does nothing for a request with no
`Origin` at all. The checks above hold unconditionally and do not rely
on it.

## GET routes have no side effect

A `GET` is the most forgeable request there is (an `<img>` tag fires
one), so no `GET` under `/h3_suite/` creates, writes, moves or deletes
anything. Project lookups open with `create=False`; the storage report
and the take cleanup preview only read; the level-match preview and the
drift report only decode; `GET /h3_suite/project/folder` reports a path
and does not open it; the exports listing and the master download only
read. There is no `os.makedirs` reachable from a `GET`.

## No programs are started by a request

The pack starts no external program. The export joins clips with PyAV
(`concat.py`) inside the server process; nothing is resolved from
`PATH` and nothing is launched. The "Copy folder path" button
reports the project's path for the user to open themselves.

## Filesystem containment

Every path that a request or a widget can influence is resolved with
`os.path.realpath` and tested with `os.path.commonpath` against its
root, so a symlink inside the tree that points out of it is refused and
a sibling folder whose name merely begins with the root's is not
mistaken for it. Containment bounds *where* a byte lands; the guard
above decides *who* may fire it. Both hold on every write.

| Surface | Root | Enforced by |
|---|---|---|
| Project name (routes and the Hub node's widget) | `output/h3_projects/<name>` | `project.validate_name` (single path component, regex) and the realpath parent check in `Project.__init__` |
| Clip files moved to trash, reopened, cleaned up | the project's `clips/` | `Project._trash_pair` (basename must match `clip_NNN_takeN`, then `Project._contained`) |
| Export filename | the project folder | `_safe_export_name` (basename, safe characters) plus a realpath dirname check in the export route |
| Import source (`rel` in list/probe/file/import) | ComfyUI's input folder | `_safe_source` (realpath + commonpath, and only a video extension) |
| Upload destination | `input/h3_imports/` | basename only, extension allow-list, written to a temp name and renamed into place |
| Clip video served to the player | the project folder | realpath + commonpath in the video route |
| Exported master listed or downloaded (`file` in exports/master) | the project root, not `clips/` | basename only, must match `_EXPORT_RE` (what the export can write, never a dotfile), realpath's parent must be the project root, so a symlink out is neither listed nor served |
| VAE names sent by the panel for a latent export | ComfyUI's `vae` folder | must appear in `folder_paths.get_filename_list("vae")` before `get_full_path` is consulted |
| `latent_path` widget on H3 Context Load Latent | ComfyUI's output folder | `_inside_output` in `nodes.py`; an absolute path elsewhere raises, it is not remapped |
| `filename_prefix` widget on H3 Context Save Latent | ComfyUI's output folder | `_contain_prefix` in `nodes.py`: a whole `..` segment raises, drive letters and UNC prefixes are stripped, then realpath + commonpath; core's `get_save_image_path` checks again |

Sanitisers here refuse rather than rewrite: a prefix that would leave
the output folder is an error the user sees, not a silently different
filename. Only whole `..` segments are rejected, so a name such as
`a..b` survives intact.

## Model loading

Two `POST` routes (export from latents, source import) load VAE
checkpoints so they can decode or encode latents. They sit behind the
guard above, and the names they accept are validated against ComfyUI's
own listing of the `vae` folder first. The latent files a route reads
come only from the project's own `clips/` folder, and only in the
safetensors format; the one node that reads a latent from a widget path
is contained to the output folder (see the table above).

## Runtime patching

The compatibility layers for older ComfyUI builds replace functions on
ComfyUI's own modules in memory, and only where the running build lacks
the native behaviour. They are ordinary function definitions in this
pack's source; no code is built from strings. Nothing is written to
disk and a restart reverts everything.

## Verification

`tests/route_guard_probe.py` mounts the real routes on a real `aiohttp`
application and checks every `POST` over HTTP: no token, wrong token,
right token but cross-site, right token but not JSON, and right token
with JSON; the token `GET` same-origin and against foreign origins; and
every `GET` with no token. It then disables the token check in place
and confirms the no-token request passes, so a suite that stays green
with the guard removed cannot happen. `tests/widget_containment_probe.py`
does the same for the two widget paths.

## Reporting

Open an issue at
https://github.com/Adudeguyman/ComfyUI-H3-Project-Suite/issues.
