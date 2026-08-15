"""The vendored PR #15375 layer must be capability-aware.

Three behaviours matter and each gets a fake ComfyUI:
  1. a core MISSING the mask engine -> the layer installs it, marked, and
     a second call installs nothing further
  2. a core that already HAS it natively -> the layer touches nothing
  3. a core with only PART of the engine -> the layer refuses loudly
     rather than mixing its snapshot with unknown half-native behaviour
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Model:
    def forward(self, x, timestep, context):
        return x

    def _forward(self, x, timestep, context):
        return x


class _Final:
    def forward(self, x, t_emb):
        return x


def fake_comfy(native=False, partial=False):
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules[name] = types.ModuleType(name)
    h3m = types.ModuleType("comfy.ldm.minimax.model")
    h3m.MiniMaxH3Model = type("MiniMaxH3Model", (), {
        "forward": _Model.forward, "_forward": _Model._forward})
    h3m.FinalLayer = type("FinalLayer", (), {"forward": _Final.forward})
    if native:
        def forward(self, x, timestep, context, denoise_mask=None,
                    audio_denoise_mask=None):
            return x
        h3m.MiniMaxH3Model.forward = forward
        h3m.MiniMaxH3Model._forward = forward
        h3m.mask_row_values = lambda *a: None
        h3m._mod_row = lambda *a: None
    if partial:
        h3m.mask_row_values = lambda *a: None   # one piece, not the rest
    sys.modules["comfy.ldm.minimax.model"] = h3m

    mb = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **kw):
            return {}
    if native:
        def process_timestep(self, timestep, denoise_mask=None,
                             audio_denoise_mask=None):
            return timestep
        MiniMaxH3.process_timestep = process_timestep
        MiniMaxH3.process_denoise_mask = lambda self, m: m
        MiniMaxH3.scale_latent_inpaint = lambda self, **kw: None
    mb.MiniMaxH3 = MiniMaxH3
    sys.modules["comfy.model_base"] = mb
    return h3m, mb


def fresh():
    for k in ("mask_compat", "mask_payload_compat"):
        sys.modules.pop(k, None)
    import importlib
    return (importlib.import_module("mask_compat"),
            importlib.import_module("mask_payload_compat"))


def main():
    # 1. missing engine -> installed and marked, idempotent
    h3m, mb = fake_comfy()
    mc, mpc = fresh()
    assert mc.ensure_h3_mask_compat() is True
    st = mc.capability_status()
    assert st["mask_engine_complete"] and st["mask_engine_compat"], st
    assert not st["mask_engine_native"]
    fwd_first = h3m.MiniMaxH3Model.forward
    mc.ensure_h3_mask_compat()
    assert h3m.MiniMaxH3Model.forward is fwd_first, "reinstalled on 2nd call"
    print("1. missing engine: installed once, marked, idempotent")

    assert mpc.ensure_av_mask_payload_compat() is True
    wrapped = mb.MiniMaxH3.extra_conds
    st2 = mpc.capability_status()
    assert st2["wrapper_present"], st2
    mpc.ensure_av_mask_payload_compat()
    assert mb.MiniMaxH3.extra_conds is wrapped, "payload double-wrapped"
    out = mb.MiniMaxH3().extra_conds()
    assert out == {}, "wrapper must still call the base"
    print("2. missing payload: wrapped once, base still called")

    # 3. native core -> nothing touched
    h3m2, mb2 = fake_comfy(native=True)
    mc2, mpc2 = fresh()
    fwd = h3m2.MiniMaxH3Model.forward
    ec = mb2.MiniMaxH3.extra_conds
    assert mc2.ensure_h3_mask_compat() is True
    assert mpc2.ensure_av_mask_payload_compat() is True
    assert h3m2.MiniMaxH3Model.forward is fwd, "native forward replaced"
    assert mb2.MiniMaxH3.extra_conds is ec, "native extra_conds wrapped"
    st3 = mc2.capability_status()
    assert st3["mask_engine_native"] and not st3["mask_engine_compat"], st3
    print("3. native core: everything left alone, native wins")

    # 4. partial-native -> loud refusal
    fake_comfy(partial=True)
    mc3, _ = fresh()
    try:
        mc3.ensure_h3_mask_compat()
    except RuntimeError as exc:
        assert "partial native" in str(exc), exc
        print("4. partial-native core: refused loudly -> %s"
              % str(exc)[:60])
    else:
        raise AssertionError("partial-native core must be refused")

    print("all checks passed")


if __name__ == "__main__":
    main()
