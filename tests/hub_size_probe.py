"""The Hub's size warning must predict the refusal, not follow it.

The panel tells you what a Resolution Selector will produce and whether
the project will accept it. That advice is only worth anything if the
arithmetic is EXACTLY core's, so this checks the mirrored formula
against the reference table the pack ships in its own example workflow,
then drives the warning itself with fake graphs.

Red is reserved for "the next queue will be refused". Anything the Hub
can live with must not be red, or the colour stops meaning anything.
"""

import json
import os
import re
import subprocess
import sys

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(_PKG, "web", "h3_project_panel.js")

# 16:9 at multiple 32, from the "Size Settings Reference" note in
# example_workflows/H3 Project Suite - chained (vanilla).json
REFERENCE = [(0.2, 608, 352), (0.3, 736, 416), (0.4, 864, 480),
             (0.5, 960, 544), (0.6, 1056, 608), (0.7, 1152, 640),
             (0.8, 1216, 672), (0.9, 1280, 736), (1.0, 1376, 768),
             (1.2, 1504, 832), (1.5, 1664, 928), (1.8, 1824, 1024),
             (2.0, 1920, 1088)]


def lift():
    """The pure helpers, out of the panel and into a runnable script."""
    src = open(PANEL, encoding="utf-8").read()
    start = src.index("const MODEL_FILE")
    end = src.index("function wiredVaeNames")
    body = src[start:end]
    for name in ("upstreamNode", "resolutionFrom", "upstreamSelector",
                 "selectorSettingFor", "hubSizeLine"):
        assert ("function %s" % name) in body, "%s is not in the block" % name
    return body


HARNESS = """
const app = { graph: null };

function selector(ratio, mp, multiple) {
  return { widgets: [{ name: "aspect_ratio", value: ratio },
                     { name: "megapixels", value: mp },
                     { name: "multiple", value: multiple }],
           inputs: [] };
}

function hub(sel) {
  const nodes = [];
  const links = {};
  const h = { inputs: [{ name: "width", link: sel ? 1 : null },
                       { name: "height", link: sel ? 2 : null }] };
  if (sel) { links[1] = [1, 99, 0, 0, 0]; links[2] = [2, 99, 1, 0, 1]; }
  h.graph = { links, getNodeById: () => sel, _nodes: nodes };
  return h;
}

const out = { table: [], cases: {} };
for (const [mp, w, h] of %(ref)s) {
  const got = resolutionFrom("16:9 (Widescreen)", mp, 32);
  out.table.push([mp, got.width, got.height, got.width === w && got.height === h]);
}
const sized = { resolution: { width: 1216, height: 672 } };
const unsized = { resolution: null };
out.cases.wrongMultiple = hubSizeLine(hub(selector("16:9 (Widescreen)", 0.8, 8)), sized);
out.cases.mismatch = hubSizeLine(hub(selector("1:1 (Square)", 0.7, 32)), sized);
out.cases.agrees = hubSizeLine(hub(selector("16:9 (Widescreen)", 0.8, 32)), sized);
out.cases.noSelector = hubSizeLine(hub(null), sized);
out.cases.declares = hubSizeLine(hub(selector("16:9 (Widescreen)", 0.8, 32)), unsized);
out.cases.nothing = hubSizeLine(hub(null), unsized);
out.suggest = selectorSettingFor(1216, 672, "1:1 (Square)");
out.suggestNone = selectorSettingFor(1000, 1000, null);
console.log(JSON.stringify(out));
"""


def main():
    script = lift() + HARNESS % {"ref": json.dumps(
        [[mp, w, h] for mp, w, h in REFERENCE])}
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr.strip()[-800:])
        raise SystemExit("the lifted panel helpers did not run")
    out = json.loads(r.stdout.strip().splitlines()[-1])

    bad = [row for row in out["table"] if not row[3]]
    assert not bad, "formula drifted from core's: %s" % bad
    print("1. formula matches all %d rows of the pack's reference table"
          % len(out["table"]))

    red = lambda s: "h3p-autowarn" in s     # noqa: E731
    c = out["cases"]

    assert red(c["wrongMultiple"]), c["wrongMultiple"]
    assert "multiple" in c["wrongMultiple"] and "32" in c["wrongMultiple"]
    print("2. multiple 8 -> red, and says to set 32")

    assert red(c["mismatch"]), c["mismatch"]
    assert "864 × 864" in c["mismatch"] and "1216 × 672" in c["mismatch"]
    # the fix it offers must actually produce the project's size
    assert "16:9 at 0.8 MP" in c["mismatch"], c["mismatch"]
    print("3. 1:1 0.7MP into a 1216x672 project -> red, naming both sizes "
          "and the setting that fixes it")

    for key in ("agrees", "noSelector", "declares", "nothing"):
        assert not red(c[key]), "%s should not be red: %s" % (key, c[key])
    assert "1216 × 672" in c["agrees"] and "1216 × 672" in c["noSelector"]
    assert "1216 × 672" in c["declares"]
    assert "first clip" in c["nothing"]
    print("4. agreeing, unwired, declaring and unset are all plain text - "
          "red only ever means the next queue fails")

    assert out["suggest"] == "16:9 at 0.8 MP", out["suggest"]
    assert out["suggestNone"] is None, out["suggestNone"]
    print("5. the suggestion prefers a real setting and stays quiet when "
          "no setting lands on the size")

    print("all checks passed")


if __name__ == "__main__":
    main()
