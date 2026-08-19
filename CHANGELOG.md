# Changelog

## 1.3.0

### Registry

The pack carries `pyproject.toml` again, so it can be published to the
Comfy Registry. It declares `av` and `numpy` - which drive export, join
level matching and the drift report - and a minimum ComfyUI of 0.33.1,
the first release where nothing needs patching. Older builds still work;
the pack patches them itself and says so.

The licence now names both the original author and this fork, and the
packs whose code it carries.

### Coexisting with the other H3 motion-context packs

Several packs in this lineage lift the same ComfyUI restriction and use
the same keyframe convention. If two of them patched the layout at once,
both corrections ran and every anchor landed twice as far along -
silently, and looking like a model problem rather than a conflict.

The pack now marks its own wrapper and recognises the others', standing
down when one of them already owns the constructor: it uses the same
convention, so it places our anchors correctly. Standing down still
counts as covered, so the node renders normally instead of refusing.
Their pack already detected ours; this closes the other direction.

### Auto-approve

A per-project toggle in the panel's top bar that turns off the review
gate: each render is approved as it arrives and the next queue extends
the chain instead of re-rolling. Off by default, remembered in
project.json, and confirmed before it can be turned on.

While it is on it is visible in three places - a red banner in the
panel, a red line on the Hub node, and a warning in the ComfyUI log on
every render - because the failure mode is a chain that grew on its own
while nobody was looking.

Takes are still recorded, so an auto-approved clip can be reopened and
re-rolled like any other.

## 1.2.0

### Keeping up with ComfyUI

Two of this pack's fixes went upstream on 13 August 2026
([#15439](https://github.com/Comfy-Org/ComfyUI/pull/15439)): interior
keyframe anchors, and letting keyframes and references coexist instead
of references quietly winning.

- The pack now detects that and stands down from the video side, keeping
  only the audio timeline placement, which was never upstreamed. On
  older ComfyUI it patches as before. The check is on what your build
  actually does rather than a version number, so it survives rebases and
  backports.
- **Fixed a two-frame skip at every join** on those newer builds. The
  pack's pinned blocks carried a placeholder index with the real one
  alongside - a habit from when interior values were rejected. Handing
  that placeholder to a core that now places anchors properly stacked
  every block on the first frame, so the model held only the opening and
  let go early, and the trim then cut real footage. The blocks now tell
  ComfyUI the truth and let it do the placing.
- When something else has replaced ComfyUI's layout, the log now names
  it instead of guessing, and `tests/who_patched_layout.py` finds which
  pack is responsible.
- `tests/new_core_probe.py` fakes both eras of ComfyUI and checks the
  pack does the right thing on each.

### seed_head at full strength

On newer ComfyUI, seed head no longer runs in its weaker form. The
pack carries the mechanism from upstream pull request
[#15375](https://github.com/Comfy-Org/ComfyUI/pull/15375) - not merged
yet - as a runtime layer vendored from the
[MultiRef fork](https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef),
GPL-3.0, the PR itself by drozbay.

Held frames now read as *given* content from the first sampling step
rather than as the model's own unfinished work, and `head_hold` grades
the conditioning as well as the picture. Nothing on disk changes: it
loads into memory when seed head first runs, a restart reverts it,
native support wins if ComfyUI ever ships this, and a partly-native
ComfyUI is refused rather than half-patched. The log says which form
you got.

### Measuring drift

**Measure drift** in the Hub samples every clip and reports how
brightness, contrast, sharpness and colour move across the chain - as a
total, as a rate per clip, and as a bar per clip for whichever moved
most. Descriptive statistics, not a quality score: they move with
content too, so the trend across a continuous scene is what to read.

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
