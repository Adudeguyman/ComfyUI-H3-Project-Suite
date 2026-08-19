"""The panel's scale control has to hold two invariants.

Font-size-only scaling works by every font-size in the sheet being
written against one custom property. A single literal font-size is a
control that silently refuses to scale, and it is invisible until
somebody on a 4K monitor turns text up and one label stays tiny.

The second invariant is the escape hatch: the scale popover redeclares
the property as 1 for its own subtree, so it stays legible at settings
that broke the rest of the layout - including the setting you would use
it to undo.
"""

import os
import re
import sys

PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "h3_project_panel.js")

VAR = "--h3p-fs"


def stylesheet(src):
    start = src.index(".h3p-overlay{")
    end = src.index("`;", start)
    return src[start:end]


def main():
    src = open(PANEL, encoding="utf-8").read()
    css = stylesheet(src)

    sizes = re.findall(r"font-size:([^;]+);", css)
    literal = [s.strip() for s in sizes if "calc(" not in s]
    assert not literal, "font-sizes that will not scale: %s" % literal
    assert all(VAR in s for s in sizes), "a calc() without the variable"
    print("all %d font-sizes scale with %s" % (len(sizes), VAR))

    # every one needs the , 1 fallback so the sheet renders before any
    # preference is applied
    bad = [s.strip() for s in sizes if "var(%s, 1)" % VAR not in s]
    assert not bad, "missing the fallback: %s" % bad
    print("every font-size carries the 1 fallback")

    # the popover re-roots the cascade for its own subtree
    menu = re.search(r"\.h3p-scalemenu\{([^}]*)\}", css)
    assert menu, "no .h3p-scalemenu rule"
    assert "%s:1" % VAR in menu.group(1).replace(" ", ""), \
        "the scale popover does not unscale itself: %s" % menu.group(1)
    print("the scale popover pins %s to 1 for itself" % VAR)

    # the property must be set on documentElement: overlays and popovers
    # attach to <body> and are not descendants of the modal
    assert re.search(r"document\.documentElement\.style\.setProperty\(\s*\n?\s*\"%s\"" % VAR,
                     src), "text scale is not set on documentElement"
    print("text scale is applied on documentElement")

    # boxes are clamped to the viewport, or the header can leave the screen
    for m in re.finditer(r"box\.style\.(width|height) = `min\(([^`]*)`", src):
        assert "vw" in m.group(2) or "vh" in m.group(2), m.group(0)
    print("scaled boxes are clamped to the viewport")

    # separate ceilings, and clamping on the way in
    assert "TEXT_SCALE_MAX = 2.0" in src and "SCALE_MAX = 3.0" in src, \
        "the two axes should not share a ceiling"
    load = src[src.index("function loadScalePrefs"):src.index("function saveScalePrefs")]
    assert load.count("clampScale(") >= 2, \
        "loadScalePrefs must clamp what it reads, not just what it writes"
    print("separate ceilings per axis, clamped on read as well as write")

    # both storage accesses wrapped: private mode throws on setItem
    for fn in ("loadScalePrefs", "saveScalePrefs"):
        body = src[src.index("function %s" % fn):]
        body = body[:body.index("\n}")]
        assert "try {" in body, "%s does not guard localStorage" % fn
    print("both localStorage accesses are guarded")

    print("all checks passed")


if __name__ == "__main__":
    main()
