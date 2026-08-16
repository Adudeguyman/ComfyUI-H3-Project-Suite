"""Every DOM handle the panel uses must be created by the same class.

The panel is one file with several modal classes that share a base and
look alike. Building a widget in one class and referencing it from
another parses fine, passes `node --check`, and then throws at runtime
the first time that class renders - which empties the whole panel,
because one exception aborts the render.

This walks each class body, collects what it assigns to `this.<name>`,
collects what it reads from `this.<name>`, and reports reads that no
class in its inheritance chain ever assigns.
"""

import os
import re
import sys

PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "h3_project_panel.js")

# handles that legitimately come from ComfyUI's node object or the DOM
ALLOWED = {
    "node", "state", "storage", "drift", "name", "size", "widgets",
    "onRemoved", "constructor", "timeline", "curSeg", "total", "video",
    "audioEl", "_esc", "_raf", "playing", "seg",
}


def class_spans(src):
    out = []
    for m in re.finditer(r"^class (\w+)(?: extends (\w+))?\s*\{", src,
                         re.M):
        start = m.end()
        depth = 1
        i = start
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        out.append((m.group(1), m.group(2), src[start:i]))
    return out


def main():
    src = open(PANEL, encoding="utf-8").read()
    spans = class_spans(src)
    assigns, parents, reads = {}, {}, {}
    for name, parent, body in spans:
        parents[name] = parent
        assigns[name] = set(re.findall(r"this\.(\w+)\s*=", body))
        reads[name] = set(re.findall(r"this\.(\w+)", body))

    def descendants(root):
        out = set()
        for child, parent in parents.items():
            cur = parent
            while cur:
                if cur == root:
                    out.add(child)
                    break
                cur = parents.get(cur)
        return out

    problems = []
    for name in assigns:
        available = set()
        cur = name
        while cur:                       # own body and its ancestors
            available |= assigns.get(cur, set())
            cur = parents.get(cur)
        # a base class may legitimately use handles its subclasses build -
        # that is the template pattern, not a wiring mistake. What this
        # probe is really hunting is a SIBLING reference: one modal
        # building a widget the other one reads.
        for child in descendants(name):
            available |= assigns.get(child, set())
        for handle in sorted(reads[name] - available - ALLOWED):
            # methods defined on the class are fine
            if re.search(r"^\s+(async\s+)?%s\s*\(" % re.escape(handle),
                         dict((n, b) for n, _p, b in spans)[name], re.M):
                continue
            bodies = dict((n, b) for n, _p, b in spans)
            related = set()
            cur = parents.get(name)
            while cur:
                related.add(cur)
                cur = parents.get(cur)
            related |= descendants(name)
            if any(re.search(r"^\s+(async\s+)?%s\s*\("
                             % re.escape(handle), bodies.get(rel, ""), re.M)
                   for rel in related):
                continue
            problems.append((name, handle))

    for name, _p, _b in spans:
        print("  %-18s assigns %2d handles" % (name, len(assigns[name])))
    if problems:
        print()
        for name, handle in problems:
            print("  %s reads this.%s but nothing in its chain creates it"
                  % (name, handle))
        raise SystemExit("panel wiring probe FAILED")
    print("every handle the panel reads is created by its own class")


if __name__ == "__main__":
    main()
