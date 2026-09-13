"""Uploads land where they should, or not at all.

The upload route takes a filename straight from a browser, so the
interesting cases are hostile or malformed rather than ordinary: a name
that climbs out of the input folder, an extension nothing can open, a
collision with an existing file, and a transfer that dies part way -
which must not leave something in the picker that looks playable.
"""

import asyncio
import os
import sys
import tempfile
import types

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

_OUT = tempfile.mkdtemp()
_IN = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: _OUT
fp.get_input_directory = lambda: _IN
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
    def json_response(data, status=200, headers=None):
        return types.SimpleNamespace(data=data, status=status,
                                     headers=headers or {})

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

pkg = types.ModuleType("h3up")
pkg.__path__ = [_PKG]
sys.modules["h3up"] = pkg
for sub in ("project", "level_match", "chain_report", "import_source",
            "export_latents", "routes"):
    spec = importlib.util.spec_from_file_location(
        "h3up." + sub, os.path.join(_PKG, sub + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["h3up." + sub] = m
    try:
        spec.loader.exec_module(m)
    except Exception as exc:          # torch-dependent modules
        if sub == "routes":
            raise
        print("   (%s not importable here: %s)" % (sub, exc))


class Part:
    def __init__(self, filename, data, name="file", fail_after=None):
        self.name = name
        self.filename = filename
        self._data = data
        self._sent = 0
        self._fail_after = fail_after

    async def read_chunk(self, size):
        if self._fail_after is not None and self._sent >= self._fail_after:
            raise IOError("connection lost")
        chunk = self._data[self._sent:self._sent + size]
        self._sent += len(chunk)
        return chunk


class Reader:
    def __init__(self, parts):
        self._parts = list(parts)

    async def next(self):
        return self._parts.pop(0) if self._parts else None


class Req:
    def __init__(self, parts):
        self._parts = parts
        self.rel_url = types.SimpleNamespace(query={})
        _routes = sys.modules["h3up.routes"]
        self.headers = {_routes.TOKEN_HEADER: _routes._TOKEN}
        self.content_type = "multipart/form-data"

    async def multipart(self):
        return Reader(self._parts)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def call(path, req):
    return _run(REGISTRY[("POST", path)](req))


UP = "/h3_suite/source/upload"


def main():
    ok = call(UP, Req([Part("clip one.mp4", b"x" * 4096)]))
    rel = ok.data["rel"]
    assert rel.startswith("h3_imports"), rel
    assert os.path.isfile(os.path.join(_IN, rel))
    print("1. accepted -> %s" % rel)

    # a name that tries to climb out keeps only its basename
    esc = call(UP, Req([Part("../../../../etc/evil.mp4", b"y" * 16)]))
    rel2 = esc.data["rel"]
    real = os.path.realpath(os.path.join(_IN, rel2))
    assert os.path.realpath(_IN) == os.path.commonpath(
        [real, os.path.realpath(_IN)]), real
    assert "etc" not in rel2, rel2
    print("2. ../ climb: kept the basename, stayed inside input (%s)"
          % rel2)

    # collisions get a suffix rather than overwriting
    again = call(UP, Req([Part("clip one.mp4", b"z" * 32)]))
    assert again.data["rel"] != rel, "an existing file was overwritten"
    print("3. same name twice -> %s" % again.data["rel"])

    # an extension nothing can open is refused
    bad = call(UP, Req([Part("payload.exe", b"MZ")]))
    assert bad.status == 400 and "not a video" in bad.data["error"]
    assert not any(f.endswith(".exe")
                   for f in os.listdir(os.path.join(_IN, "h3_imports")))
    print("4. .exe refused, nothing written")

    # a transfer that dies leaves no playable file behind
    before = set(os.listdir(os.path.join(_IN, "h3_imports")))
    dead = call(UP, Req([Part("half.mp4", b"q" * 8192, fail_after=100)]))
    assert dead.status == 500
    after = set(os.listdir(os.path.join(_IN, "h3_imports")))
    assert after == before, "a partial upload was left behind: %s" % (
        after - before)
    print("5. interrupted upload: no leftover, not even a .part")

    # and the listing only shows real videos
    lst = _run(REGISTRY[("GET", "/h3_suite/source/list")](Req([])))
    names = [f["rel"] for f in lst.data["files"]]
    assert all(n.lower().endswith(
        (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")) for n in names)
    assert len(names) >= 3, names
    print("6. listing: %d videos, nothing else" % len(names))

    # a file that is not a video is never probed or served, even from
    # inside the input folder
    open(os.path.join(_IN, "notes.txt"), "w").write("secret")
    for path in ("/h3_suite/source/probe", "/h3_suite/source/file"):
        req = Req([])
        req.rel_url.query = {"rel": "notes.txt"}
        res = _run(REGISTRY[("GET", path)](req))
        assert res.status in (400, 404) and "not a video" in res.data["error"], (
            path, getattr(res, "data", res))
    print("7. a non-video inside the input folder is refused by probe and file")

    print("all checks passed")


if __name__ == "__main__":
    main()
