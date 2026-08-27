# Changelog

## Unreleased

### ComfyUI 0.34: nothing patched, older cores unchanged

ComfyUI 0.34.0 places interior keyframe anchors itself and lets a
keyframe carry audio, placed on the audio grid at the keyframe's
instant - which is everything this pack's remaining runtime patch was
for. On such a core the pack now installs NOTHING: the pinned audio is
emitted as a plain keyframe whose fractional anchor puts the window's
end exactly where the wrapper used to put it.

Detection stays behavioural rather than reading a version string, so
backports and forks land on the right path automatically: the full
patch on 0.31-0.33.0, the audio-only wrapper on #15439-era masters, and
nothing at all on 0.34 behaviour. The 0.34 check includes a canary
borrowed from NikoDemon80's 0.4.0: a fractional NEGATIVE anchor must be
placed literally, because no stock node produces one - an innocent
int() cast added upstream later would silently move every pinned sound,
and the pack refuses the native path if that ever happens.

### Master quality, and cheaper review clips

Exporting from latents now asks how to encode the master - High (CRF
16), Archive (CRF 14, slow) or Quick (CRF 18) - because that file is the
deliverable and was previously inheriting a default meant for review
copies.

The per-clip videos moved to the "fast" x264 preset at the same CRF,
roughly halving the wait after each render for a little file size. They
are review copies; when the master is built from latents nothing in the
delivered video passes through them at all.

### Long chains export without swapping

The latent export had the same whole-clip conversion the mp4 writer did:
scaling a decoded clip as a batch, plus a 144-frame copy for level
matching. Three gigabytes of frames became about ten. It decodes one
clip at a time, so this was a per-clip ceiling rather than a per-chain
one, but a long clip would have hit it exactly as saving did.

Frames are now scaled, corrected and encoded individually, and level
matching runs from per-frame statistics rather than scaled copies of the
footage - it only ever needed a few hundred numbers.

### Saving long clips no longer crawls

The mp4 writer converted every frame in one batch - clip(), then times
255, then round(), then astype - which makes four full-size temporaries.
About 13 GB for a thirteen-second 928x928 clip against 3 GB of actual
frames. Short clips fit and saved in a second; longer ones pushed the
machine into swap, and that presents as a ten-minute encode with an idle
CPU rather than as an out-of-memory error, so the symptom points away
from the cause.

Frames are now converted one at a time, so peak memory is a frame rather
than a clip. Project Save and the mp4 writer also log their phase
timings when a save takes more than two seconds.

### Importing footage

**Import...** in the panel opens a picker over ComfyUI's input folder
and a filmstrip of the chosen file, with the kept span lit and the
dropped ends dimmed. The span is dragged, the video scrubs underneath,
and the length picker offers only lengths H3 can render - so the trim
is chosen by looking at it rather than described afterwards. It opens
on the longest valid window anchored at the end, since imported footage
usually runs into a chain.

The Project Hub node gained optional `vae` and `audio_vae` inputs. They
render nothing; the panel reads the graph to see which loaders feed them
and encodes imported footage with those, so importing works on an empty
project in a fresh session with nothing queued. Exporting from latents
uses the same source.

Files arrive by drag and drop onto the window, or through a normal file
picker; both copy into ComfyUI's input folder, which is also still
browsed for anything already there. Anything not at 24 fps is remapped
by picking frames rather than blending them. Audio comes along when the file has one. The result is
written as a normal take, pending review.

There is also an **H3 Import Source** node for graph use, with the same
conforming and a written report.

### Export from latents

A **from latents** toggle beside the export buttons. The master is
assembled by decoding each approved take's saved latent and encoding
once, instead of joining the clip videos: every delivered frame meets
H.264 exactly once, and level matching is applied in float before that
encode, so corrected joins stop costing a second compression
generation.

It borrows the running graph's VAEs when a clip has been queued this
session, and otherwise loads the ones the takes were rendered with,
named in each take's recorded workflow. Nothing to wire, and no
requirement to generate something before exporting. Each candidate is
classified by what it loads as rather than by its filename, so an
unrelated VAE is refused instead of quietly decoding to garbage.

A missing latent is named and refused before a single frame is written -
a master that silently swapped one clip to its video would misrepresent
itself.

### Panel scaling

A **Scale** control in the panel's top bar, with independent window and
text axes. Window resizes the box; text changes font size only, so the
layout rewraps rather than showing less of itself - the two answer
different questions ("I want more room" against "I can't read this") and
a single zoom can only satisfy one.

Set-then-Apply rather than live, because the control sits inside the
window it resizes and a live update moves the slider out from under the
pointer. Apply highlights when there is something to apply; Reset returns
both to 100%. Scaled boxes stay clamped to the viewport, so a large
setting can never push the control that undoes it off-screen, and the
popover pins itself to 100% so it stays legible at every setting.

Stored per browser in localStorage, not in the workflow: scale belongs to
a monitor, not to a project.

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
