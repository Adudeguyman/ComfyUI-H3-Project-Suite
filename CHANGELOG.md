# Changelog

## 1.1.0

### Levelling a join

A chained clip can open slightly brighter than the one before it and
settle back over a second or so - the model relaxing toward its own
exposure once it stops being held to the previous clip. **Level-match
join** measures that step, tells you what it found, and corrects it on
export.

- Per join, not per project: joins differ, and a global switch would
  correct seams that were never broken.
- Measures before you commit and reports the step, the settle time and
  the gain it would apply.
- Your clips are never modified; only the exported master. An export
  containing a corrected join is re-encoded rather than stream-copied.
- Refuses steps beyond 35%, which are cuts or intended lighting changes
  rather than seam artifacts.
- Consecutive corrected joins do not compound, and the export says so
  when one correction is still active at its clip's tail.

### Seeing what you are watching

- The playhead now names the take, not just the clip: `clip 7 - take
  9/11`, plus `pending` or `levelled` when they apply. Hovering gives
  the basename and what the clip continues from.

### seed_head, on by default

`H3 Context` gains a third output, `latent`, and a `seed_head` toggle
which is ON by default. The pinned steps are written into the clip's
starting latent and held there during sampling, so the sampler's
trajectory begins from the previous clip's state rather than from noise
merely conditioned toward it.

**This needs the node's `latent` output wired into the sampler's
`latent_image`.** Both example workflows now are. A hand-built graph
that still feeds the sampler from the MiniMax node is unaffected: the
setting simply does nothing.

`head_hold` controls how firmly the seeded head is held; lower values
let the model repaint it slightly.

`video_source` now defaults to `latent`, which is what every example
workflow and the whole project layer already used.

### Measuring drift

**Measure drift** in the Hub samples every clip and reports how
brightness, contrast, sharpness and colour move across the chain - as a
total, as a rate per clip, and as a bar per clip for whichever moved
most. Descriptive statistics, not a quality score: they move with
content too, so the trend across a continuous scene is what to read.

### seed_head at full strength

On PR-15439 cores, seed_head now activates a vendored runtime layer
carrying the PR #15375 mechanism (per-row cond-timestep for masked
rows), from seitanism's MultiRef fork of the NikoDemon80 lineage,
GPL-3.0, credit drozbay/AbleJones for the upstream PR. Capability-
aware: native support wins, only missing pieces install, in memory,
restart reverts, partial-native cores are refused loudly. Held rows
now read as given content from step one, and head_hold grades the
conditioning. Older cores keep stock seed_head unchanged.

### Diagnostics

- `tests/video_seam_probe.py` classifies a join as duplicate, skip,
  drift, flash or clean, measured against the motion already present.
- `tests/seam_level_match.py` writes a joined A+B file with the
  correction applied, and `--also-plain` writes the same join without
  it for comparison.

### Compatibility

`seed_head` and `head_hold` are appended after the existing widgets, so
saved workflows keep their values.

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
