"""Conforming outside footage is arithmetic that fails quietly.

A frame-rate remap that drifts, a trim that lands off H3's ladder, or an
audio length that rounds the wrong way all produce a clip that renders
fine and then stutters at the first join. Each is checked here against
the grids directly, plus the two refusals that should be sentences
rather than tracebacks.
"""

import os
import sys

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "h3imp", os.path.join(_PKG, "import_source.py"))
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


def main():
    # --- the ladder ---
    valid = imp.valid_lengths(200)
    assert valid[:4] == [5, 22, 39, 56], valid[:4]
    for n in valid:
        assert (n - 5) % 17 == 0
    assert imp.snap_length(60) == 56
    assert imp.snap_length(56) == 56
    assert imp.snap_length(4) == 0
    print("1. ladder: %s ... every length is 17m+5" % valid[:4])

    # --- frame rate: selection, never invention ---
    idx = imp.cfr_index_map(100, 24.0)
    assert idx == list(range(100)), "24 fps must pass through untouched"
    idx = imp.cfr_index_map(300, 30.0)
    assert len(idx) == 240, len(idx)
    assert all(0 <= i < 300 for i in idx), "an index left the source"
    assert idx == sorted(idx), "frame order changed"
    # every output frame is a real source frame
    assert len(set(idx)) <= 300
    idx = imp.cfr_index_map(1000, 29.97)
    assert len(idx) == 801, len(idx)
    print("2. 30->24 gives 240 of 300, 29.97 gives 801 of 1000, in order")

    # --- the plan keeps what it says it keeps ---
    p = imp.plan(1000, 29.97, align="tail")
    assert p["resampled"] == 801 and p["keep"] == 787, p
    assert (p["keep"] - 5) % 17 == 0
    assert p["end"] - p["start"] == p["keep"]
    assert p["end"] == p["resampled"], "tail align must keep the END"
    ph = imp.plan(1000, 29.97, align="head")
    assert ph["start"] == 0, "head align must keep the BEGINNING"
    pc = imp.plan(1000, 29.97, align="center")
    assert pc["start"] == (801 - 787) // 2, pc["start"]
    print("3. align: tail keeps the end, head the start, center splits %d"
          % p["drop"])

    # --- the report names the cost ---
    text = imp.describe(p)
    for want in ("29.97", "801", "787", "14 frames", "0.58s"):
        assert want in text, "the report never mentions %r:\n%s" % (want,
                                                                   text)
    assert "nothing blended" in text
    print("4. report: %s" % text.splitlines()[1])

    # --- too short to render at all ---
    try:
        imp.plan(3, 24.0)
    except ValueError as exc:
        assert "fewer than the 5" in str(exc), exc
        print("5. four frames of source: refused with the reason")
    else:
        raise AssertionError("must refuse footage shorter than one step")

    # --- audio conforms to the kept video, or is refused ---
    class W:
        def __init__(self, n):
            self.shape = (1, 2, n)

        def __getitem__(self, k):
            n = self.shape[-1]
            sl = k[-1]
            start = sl.start or 0
            return W(n - start)

    sr = 32000
    frames = 787
    want = int(round(frames / 24.0 * sr))
    out, note = imp.conform_audio(W(want + 5000), sr, sr, frames,
                                  lambda w, a, b: w)
    assert out.shape[-1] == want, (out.shape, want)
    assert "last" in note
    print("6. long audio: trimmed to the kept video's %.2fs"
          % (want / float(sr)))

    try:
        imp.conform_audio(W(want // 2), sr, sr, frames,
                          lambda w, a, b: w)
    except ValueError as exc:
        assert "probably is not this video" in str(exc), exc
        print("7. half-length audio: refused instead of stretched")
    else:
        raise AssertionError("mismatched audio must be refused")

    print("all checks passed")


if __name__ == "__main__":
    main()
