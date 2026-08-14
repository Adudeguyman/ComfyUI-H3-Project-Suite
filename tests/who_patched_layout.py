"""Which custom node has replaced ComfyUI's PackedLayout?

Run this from inside your ComfyUI folder, with the same Python:

    cd ~/ComfyUI2
    python custom_nodes/ComfyUI-H3-Project-Suite/tests/who_patched_layout.py

It imports each custom node one at a time, in the order ComfyUI loads
them, and reports the first one that changes
`comfy.ldm.minimax.model.PackedLayout.__init__` away from stock - or
changes its signature.

Why this exists: several packs wrap H3's layout. When one of them
narrows the signature, everything downstream fails in a way that reads
like a ComfyUI change, and the log only shows whoever noticed first.
"""

import importlib
import inspect
import os
import sys


def describe(fn):
    mod = getattr(fn, "__module__", "?")
    qual = getattr(fn, "__qualname__", getattr(fn, "__name__", "?"))
    try:
        sig = str(inspect.signature(fn))
    except (TypeError, ValueError):
        sig = "(unintrospectable)"
    return mod, qual, sig


def main():
    here = os.getcwd()
    if not os.path.isdir(os.path.join(here, "comfy")):
        print("run this from your ComfyUI folder (the one containing "
              "comfy/ and custom_nodes/)")
        return 1
    sys.path.insert(0, here)
    try:
        import comfy.ldm.minimax.model as mm
    except Exception as exc:
        print("could not import comfy.ldm.minimax.model: %s" % exc)
        return 1

    base = mm.PackedLayout.__init__
    b_mod, b_qual, b_sig = describe(base)
    print("stock PackedLayout.__init__")
    print("   %s.%s%s\n" % (b_mod, b_qual, b_sig))
    stock_takes_fc = "frame_count" in b_sig

    cn = os.path.join(here, "custom_nodes")
    names = sorted(n for n in os.listdir(cn)
                   if os.path.isdir(os.path.join(cn, n))
                   and not n.startswith(".")
                   and os.path.exists(os.path.join(cn, n, "__init__.py")))
    sys.path.insert(0, cn)

    culprits = []
    for name in names:
        before = mm.PackedLayout.__init__
        try:
            importlib.import_module(name)
        except Exception:
            continue          # import failures are not what we are hunting
        after = mm.PackedLayout.__init__
        if after is before:
            continue
        a_mod, a_qual, a_sig = describe(after)
        takes_fc = "frame_count" in a_sig or "**" in a_sig
        flag = "" if takes_fc or not stock_takes_fc else \
            "   <-- DROPS frame_count"
        print("%-46s replaced it" % name)
        print("   now %s.%s%s%s" % (a_mod, a_qual, a_sig, flag))
        culprits.append((name, takes_fc))

    print()
    if not culprits:
        print("nothing replaced PackedLayout during import. If the error "
              "still happens, the patch is being applied later - at "
              "execution rather than import - so try disabling the H3 "
              "packs one at a time.")
        return 0
    narrow = [n for n, ok in culprits if not ok]
    if narrow:
        print("These narrowed the signature and are the ones to disable "
              "first: %s" % ", ".join(narrow))
    else:
        print("All replacements kept a compatible signature: %s"
              % ", ".join(n for n, _ in culprits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
