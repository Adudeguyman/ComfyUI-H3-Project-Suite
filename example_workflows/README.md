# Example workflows

Workflows for the project layer go here.

A working chain needs four nodes wired up: **H3 Project Hub**, **H3 Context**,
**H3 Context Trim** and **H3 Project Save**. The three connections that matter
are Hub → Context (twice) and Hub → Save; see the
[README](../README.md#the-four-nodes) for the layout.

## Credits carried over

The Ref2VA multi-reference and audio compatibility fix that this pack relies
on was contributed by **seitanism** in the Banodoco MiniMax H3
seamless-extension thread ([patch](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771676158206032),
[workflow](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771814452793474),
shared 2026-08-08), by way of
[ethanfel's fork](https://github.com/ethanfel/ComfyUI-H3-Motion-Context). It
is built into this pack — do not run the separately posted patch script on
this version.

## Notes for whatever lands here

- Set **H3 Context** to `video_source: latent`. Its `vae` input can stay
  unwired on that path.
- **H3 Context Trim**'s `fps` must match the frame rate you render and export
  at (24 for H3).
- Nothing needs bypassing for clip 1 — the Hub's `chain_active` output handles
  it.
- Demos that use rgthree-comfy or VideoHelperSuite nodes should say so here,
  since those are extra installs.
