"""Every POST route must read the project name the way the panel sends it.

The panel posts a JSON body: {"name": ..., ...}. A route that instead
reads the query string gets an empty name and fails with a confusing
"project name '' is not allowed", which reads like a naming problem
rather than a wiring one.

This registers the real routes against a fake server, then calls each
POST the way the panel does - JSON body, no query string - and requires
it to find the project.
"""

import asyncio
import os
import sys
import tempfile
import types

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

_OUT = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: _OUT
sys.modules["folder_paths"] = fp

REGISTRY = {}


class _Routes:
    def _add(self, method, path):
        def deco(fn):
            REGISTRY[(method, path)] = fn
            return fn
        return deco

    def get(self, path):
        return self._add("GET", path)

    def post(self, path):
        return self._add("POST", path)


class _Web:
    @staticmethod
    def json_response(data, status=200):
        return types.SimpleNamespace(data=data, status=status)

    class FileResponse:
        def __init__(self, path):
            self.path = path


aio = types.ModuleType("aiohttp")
aio.web = _Web
sys.modules["aiohttp"] = aio
sys.modules["aiohttp.web"] = _Web
srv = types.ModuleType("server")
srv.PromptServer = types.SimpleNamespace(
    instance=types.SimpleNamespace(routes=_Routes()))
sys.modules["server"] = srv

import importlib.util  # noqa: E402

pkg = types.ModuleType("h3rc")
pkg.__path__ = [_PKG]
sys.modules["h3rc"] = pkg
for sub in ("project", "level_match", "chain_report", "routes"):
    spec = importlib.util.spec_from_file_location(
        "h3rc." + sub, os.path.join(_PKG, sub + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["h3rc." + sub] = m
    spec.loader.exec_module(m)

from h3rc.project import Project  # noqa: E402


class FakeRequest:
    """What the panel's post() produces: a JSON body, no query string."""

    def __init__(self, body):
        self._body = body
        self.rel_url = types.SimpleNamespace(query={})

    async def json(self):
        return self._body

    def get(self, key, default=None):
        return default


def call(method, path, body):
    handler = REGISTRY[(method, path)]
    return asyncio.get_event_loop().run_until_complete(
        handler(FakeRequest(body)))


def main():
    name = "RouteProbe"
    Project(_OUT, name, create=True)

    posts = sorted(p for (m, p) in REGISTRY if m == "POST")
    print("POST routes registered: %d" % len(posts))

    # auto_approve is the one this probe was written for, but the check
    # is generic: hand each route a body-only request and require it to
    # resolve the project rather than complain about an empty name
    checked = 0
    missing = []
    for path in posts:
        if path.endswith(("/create", "/branch", "/export", "/purge_trash",
                          "/cleanup_takes", "/reject", "/delete")):
            continue          # destructive or needs extra state
        body = {"name": name}
        if path.endswith("/auto_approve"):
            body["on"] = True
        res = call("POST", path, body)
        data = getattr(res, "data", {})
        if isinstance(data, dict) and "error" in data:
            err = str(data["error"])
            # the failure this probe exists to catch: the route looked
            # somewhere other than the body and found no name at all
            assert "is not allowed" not in err, (
                "%s could not read the name from the body: %s" % (path, err))
            # anything else is this probe not supplying a route's other
            # arguments, which is fine - it still proves the name arrived
            missing.append((path, err.split("\n")[0][:60]))
        checked += 1
    print("checked %d POST routes read the name from the body" % checked)
    for path, err in missing:
        print("   (%s needs more than a name: %s)" % (path.split("/")[-1],
                                                      err))

    # and auto_approve specifically round-trips
    res = call("POST", "/h3_suite/project/auto_approve",
               {"name": name, "on": True})
    assert res.data.get("auto_approve") is True, res.data
    assert Project(_OUT, name).auto_approve is True
    res = call("POST", "/h3_suite/project/auto_approve",
               {"name": name, "on": False})
    assert res.data.get("auto_approve") is False, res.data
    print("auto_approve: on and off both round-trip through the route")

    print("all checks passed")


if __name__ == "__main__":
    main()
