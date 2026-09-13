"""Who may fire a state-changing route, checked against a real server.

ComfyUI has no login, so every POST under /h3_suite/ stands behind three
checks: not cross-site, carries the session token, says it is JSON. This
mounts the real routes on a real aiohttp application and drives them
over HTTP, so what is measured is the wire behaviour and not a fake:

  every POST      no token -> 403 with token_required; wrong token -> 403;
                  right token but cross-site -> 403; right token but not
                  JSON -> 415; right token and JSON -> anything BUT 403/415
                  (the handler then complains about the body, which is
                  the proof the guard stepped aside)
  the upload      no token -> 403; token + multipart -> not 403/415
  the token GET   same-origin -> 200 with Cache-Control: no-store; a
                  foreign Origin, Origin: null, or Sec-Fetch-Site:
                  cross-site -> 403
  every GET       works with no token at all, since a GET has no side
                  effect to protect

Finally the guard is switched off in place and the no-token case is
re-run: it must then PASS through, proving the suite measures the guard
and would go red without it.
"""

import asyncio
import os
import sys
import tempfile
import types

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

_OUT = tempfile.mkdtemp()
_IN = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: _OUT
fp.get_input_directory = lambda: _IN
sys.modules["folder_paths"] = fp

TABLE = web.RouteTableDef()
srv = types.ModuleType("server")
srv.PromptServer = types.SimpleNamespace(
    instance=types.SimpleNamespace(routes=TABLE))
sys.modules["server"] = srv

import importlib.util  # noqa: E402

pkg = types.ModuleType("h3rg")
pkg.__path__ = [_PKG]
sys.modules["h3rg"] = pkg
for sub in ("project", "routes"):
    spec = importlib.util.spec_from_file_location(
        "h3rg." + sub, os.path.join(_PKG, sub + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["h3rg." + sub] = m
    spec.loader.exec_module(m)

routes_mod = sys.modules["h3rg.routes"]
Project = sys.modules["h3rg.project"].Project
TOKEN = routes_mod._TOKEN
HDR = routes_mod.TOKEN_HEADER


def _routes():
    posts, gets = [], []
    for r in TABLE:
        (posts if r.method == "POST" else gets).append(r.path)
    return sorted(posts), sorted(gets)


async def run():
    Project(_OUT, "GuardProbe", create=True)
    app = web.Application()
    app.add_routes(TABLE)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    host = "%s:%d" % (server.host, server.port)
    same = "http://" + host
    posts, gets = _routes()
    assert posts, "no POST routes registered"
    checks = 0
    try:
        # -- the token GET -------------------------------------------
        r = await client.get("/h3_suite/token", headers={"Origin": same})
        assert r.status == 200, r.status
        assert (await r.json())["token"] == TOKEN
        assert r.headers.get("Cache-Control") == "no-store"
        r = await client.get("/h3_suite/token")            # a shell: no Origin
        assert r.status == 200
        for bad in ({"Origin": "http://evil.example"},
                    {"Origin": "null"},
                    {"Origin": "http://%s.evil.example" % server.host},
                    {"Origin": "http://%s:1" % server.host},
                    {"Sec-Fetch-Site": "cross-site"},
                    {"Sec-Fetch-Site": "same-site"},
                    {"Origin": same, "Sec-Fetch-Site": "cross-site"}):
            r = await client.get("/h3_suite/token", headers=bad)
            assert r.status == 403, (bad, r.status)
            checks += 1
        # a default-port mismatch on one side is tolerated
        r = await client.get("/h3_suite/token",
                             headers={"Origin": "http://" + server.host,
                                      "Host": server.host})
        assert r.status == 200, r.status
        print("token GET: same-origin only, no-store (%d refusals)" % checks)

        # -- every POST ----------------------------------------------
        body = {"name": "GuardProbe"}
        json_hdr = {"Content-Type": "application/json"}
        for path in posts:
            r = await client.post(path, json=body)
            assert r.status == 403, (path, r.status)
            assert (await r.json()).get("token_required") is True, path
            r = await client.post(path, json=body, headers={HDR: "x" * 43})
            assert r.status == 403, (path, r.status)
            r = await client.post(path, json=body,
                                  headers={HDR: TOKEN,
                                           "Origin": "http://evil.example"})
            assert r.status == 403, (path, "foreign origin", r.status)
            r = await client.post(path, json=body,
                                  headers={HDR: TOKEN,
                                           "Sec-Fetch-Site": "cross-site"})
            assert r.status == 403, (path, "cross-site", r.status)
            if path.endswith("/upload"):
                r = await client.post(path, data={"file": b"x"},
                                      headers={HDR: TOKEN})
                assert r.status not in (403, 415), (path, r.status)
                checks += 5
                continue
            r = await client.post(path, data="{}",
                                  headers={HDR: TOKEN,
                                           "Content-Type": "text/plain"})
            assert r.status == 415, (path, "text/plain", r.status)
            r = await client.post(path, data='{"name": "GuardProbe"}',
                                  headers={HDR: TOKEN, **json_hdr})
            assert r.status not in (403, 415), (path, r.status,
                                                await r.text())
            checks += 6
        print("%d POST routes: refused without the token, cross-site or "
              "non-JSON; admitted with all three" % len(posts))

        # -- every GET -----------------------------------------------
        for path in gets:
            if "{" in path:
                continue
            r = await client.get(path, params={"name": "GuardProbe"})
            assert r.status != 403, (path, r.status)
            checks += 1
        print("%d GET routes: no token needed" % len(gets))

        # -- exported masters: listed, downloadable, and nothing else ----
        root = Project(_OUT, "GuardProbe").root
        with open(os.path.join(root, "GuardProbe_master.mp4"), "wb") as fh:
            fh.write(b"MASTER-BYTES")
        with open(os.path.join(root, "GuardProbe_preview.mp4"), "wb") as fh:
            fh.write(b"PREVIEW")
        with open(os.path.join(root, ".hidden.mp4"), "wb") as fh:
            fh.write(b"tmp")
        os.symlink("/etc/hostname", os.path.join(root, "escape.mp4"))
        r = await client.get("/h3_suite/project/exports",
                             params={"name": "GuardProbe"})
        listed = [e["file"] for e in (await r.json())["exports"]]
        assert set(listed) == {"GuardProbe_master.mp4",
                               "GuardProbe_preview.mp4"}, listed
        r = await client.get("/h3_suite/project/master",
                             params={"name": "GuardProbe",
                                     "file": "GuardProbe_master.mp4"})
        assert r.status == 200 and await r.read() == b"MASTER-BYTES"
        assert r.headers["Content-Disposition"].startswith("attachment"), \
            r.headers.get("Content-Disposition")
        for bad in ("../project.json", "project.json", ".hidden.mp4",
                    "escape.mp4", "clips/clip_001_take1.mp4",
                    "GuardProbe_master.mp4/../project.json"):
            r = await client.get("/h3_suite/project/master",
                                 params={"name": "GuardProbe", "file": bad})
            assert r.status == 404, (bad, r.status)
            checks += 1
        print("exports: two masters listed, the master downloads as an "
              "attachment, and the manifest, a dotfile, a symlink out, a "
              "clip and a traversal are all refused")

        # -- the guard must be what the suite measures ----------------
        orig = routes_mod.token_ok
        routes_mod.token_ok = lambda headers: True
        try:
            r = await client.post(posts[0], json=body)
            assert r.status != 403, (
                "with the token check disabled the no-token POST still got "
                "403 - the suite is not measuring the guard")
        finally:
            routes_mod.token_ok = orig
        r = await client.post(posts[0], json=body)
        assert r.status == 403
        print("guard disabled -> no-token POST passes; restored -> refused "
              "again (the suite measures the guard)")
    finally:
        await client.close()
    return checks


def main():
    # the pure origin logic, on headers the server never sees in a test
    cs = routes_mod.is_cross_site
    assert not cs({"Host": "[::1]:8188", "Origin": "http://[::1]:8188"})
    assert cs({"Host": "[::1]:8188", "Origin": "http://[::2]:8188"})
    assert not cs({"Host": "localhost:8188", "Origin": "http://LOCALHOST:8188"})
    assert not cs({"Host": "localhost", "Origin": "http://localhost:80"})
    assert cs({"Host": "localhost:8188", "Origin": "http://localhost:8189"})
    assert cs({"Host": "localhost:8188", "Origin": "http://localhost.evil:8188"})
    assert cs({"Host": "localhost:8188", "Origin": "garbage"})
    assert not cs({})
    # the hand parser's edges: a path after the authority is ignored, a
    # bare ipv6 host without a port matches itself, and anything a
    # browser would never send as its own origin counts as foreign
    assert not cs({"Host": "localhost:8188",
                   "Origin": "http://localhost:8188/some/path"})
    assert not cs({"Host": "[::1]", "Origin": "http://[::1]"})
    assert cs({"Host": "localhost:8188", "Origin": "http://evil@localhost:8188"})
    assert cs({"Host": "localhost:8188", "Origin": "http://[::1"})
    assert cs({"Host": "localhost:8188", "Origin": "http://localhost:81x88"})
    assert cs({"Host": "localhost:8188", "Origin": "localhost:8188"})
    print("is_cross_site: ipv6, case, default port, wrong port, prefix "
          "domain, garbage, no headers")
    n = asyncio.new_event_loop().run_until_complete(run())
    print("all checks passed (%d wire checks)" % n)


if __name__ == "__main__":
    main()
