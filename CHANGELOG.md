# Changelog

## 1.0.0

First release of the project suite. Everything below is on top of the
chain nodes inherited from
[ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context).

### The chain

- **Latent video path.** `video_source: latent` slices the pinned run
  straight out of the previous clip's saved latent, so no decode and no
  re-encode sits between clips. The phase-0 boundary requirement is
  enforced rather than assumed.
- **Multi-reference fix** merged from
  [ethanfel's fork](https://github.com/ethanfel/ComfyUI-H3-Motion-Context)
  (contributed by seitanism), so Ref2VA voice and character references
  survive every clip instead of being dropped after clip one.
- **Signed audio overhang.** H3 rounds the audio grid to nearest, not up,
  so a clip can end a fraction of a step short. Pinned audio is placed
  with the sign, not just the magnitude.
- **Context length** is a dropdown of the values that exist on the VAE
  grid: 1, 5, 22, 39, 56.
- **`enabled` passthrough.** Clip 1 needs no bypass ritual; the Hub's
  `chain_active` output disarms the whole path.
- **`vae` is optional**, required only for the `frames` path.
- **Patches arm on first execution**, not at import, so a stock H3
  workflow in the same session stays stock.

### The project layer

- **H3 Project Hub / H3 Project Save.** A chain lives in one folder with
  its own manifest; clip indices, filenames and the clip-1 special case
  are handled for you.
- **Review panel** with a continuous scrubbable timeline across all
  clips, single-clip mode, and a thumbnail rail.
- **Approve / Reject.** Approving is what arms the chain; nothing
  pending ever conditions the next render.
- **Takes.** Re-rolls are kept and switchable, with a preview of each.
- **Reopen** un-approves an earlier clip, offering three outcomes: back
  the chain up into a separate project first, discard the later clips,
  or branch instead. It works even while a later clip awaits review.
- **Branching** forks a chain at any clip into an independent project,
  including from a take that was never approved and one recovered from
  `.trash/`. Files are copied, not linked.
- **Export** concatenates approved clips, never overwriting: the next
  free filename is suggested and editable.
- **Housekeeping.** Storage is reported in the footer; cleanup moves
  alternate takes to `.trash/`, purge is the permanent delete. Nothing
  is ever unlinked without passing through trash first.
- **Clip metadata.** Every clip is saved with the workflow and prompt
  that produced it, in a sidecar JSON, the safetensors metadata, and the
  mp4 container tags.

### Safety

- Realpath containment and basename validation before any file is moved.
- Atomic manifest writes; invariants checked on load.
- Self-tests on the patched layout arithmetic; the pack refuses to run
  a path it cannot verify rather than rendering something wrong.
