# H3 Motion Context

Chain MiniMax H3 clips together so that motion **and sound** continue across
the joins, instead of every clip re-deciding what's happening from a single
still frame.

Generate clip A. Feed its last frames and audio into this node. Generate
clip B. B picks up exactly where A left off: same motion, same direction and
speed, and the same audio - not similar audio, the *same waveform*,
continued. Repeat down a chain as long as you like.

This is done entirely with runtime patches. No ComfyUI files are edited on
disk. Drop the folder in, restart, and if a future ComfyUI update changes
something underneath, the patches detect it at startup and refuse to run
rather than quietly rendering something wrong.

## How is this different from LTX motion context?

LTX ships clip-chaining as a built-in feature: you pin frames from the
previous clip into the latent and the model continues them. H3 has no such
feature - but it turns out the machinery was already there. H3's keyframe
system tags frames with a time coordinate and re-injects them at every
sampling step. The only thing preventing a *run* of consecutive frames was
a single check in ComfyUI that rejected any keyframe that wasn't the first
or last frame. Mathematically, the position formula already worked for every
frame in between. This project lifts that restriction (and verifies its own
math against ComfyUI's at every startup).

The bigger difference is audio. H3 generates picture and sound together,
and this carries **both** streams across the join. Getting audio to
genuinely continue - rather than the model playing a sound-alike - turned
out to be the hard part, and the fix is the most interesting thing in the
repo (see "The audio story" below).

## Install

Drop the folder into `ComfyUI/custom_nodes/` and restart. Watch the console
for:

```
h3_motion_context: interior keyframe anchors enabled
h3_motion_context: keyframe/ref coexistence enabled
```

If a self-test fails instead, the reason is logged and the nodes refuse to
run. That's deliberate: a loud failure beats a subtly wrong render.

## Wiring

```
MiniMaxH3ImageToVideo / MiniMaxH3ReferenceToVideo (or the t2v path)
  -> H3 Motion Context      <- previous clip's frames + audio
  -> guider / sampler
  ...
  decoded IMAGE + AUDIO
  -> H3 Motion Context Trim         <- wire trim_frames across
  -> Create Video / save
```

Feed `context_frames` the decoded frames of the previous clip. For audio,
the best source is the previous clip's latent - but note you **cannot**
wire the sampler's output directly into `context_latent`; ComfyUI will
flag a circular connection, correctly, because the latent you need is from
the previous *run*, not the current one. Two helper nodes carry it across
runs the same way you already carry frames and audio through saved files:

```
this run:   SamplerCustomAdvanced -> H3 Motion Context Save Latent
next run:   H3 Motion Context Load Latent -> context_latent
```

Both nodes have a `clip_index`, and the numbers mean exactly what they
say: on the Load node, the clip to CONTINUE FROM; on the Save node, the
clip THIS is. Generating clip 2 from clip 1: Load 1, Save 2. Don't like
the result? Queue again and change nothing - the retry reloads clip 1 and
overwrites clip 2's reject. Accept it, bump both numbers, move on. Files
get the natural names (`clip_00002.safetensors` is clip 2). At the
default of 0 the loader instead takes the newest file in the folder,
which is NOT retry-safe - a re-roll loads its own rejected audio - and
auto-saved files are numbered by RUN, not clip, marked by a trailing
underscore (`clip_00002_.safetensors`) so indexed loading never confuses
them for real slots. Leave context unwired for clip 1. The loader can
also point straight at a specific file, which ignores the index. (Stock Save/Load
Latent won't work here; it can't handle H3's paired video/audio latent.)
The loader's output is only for `context_latent`; don't wire it into a
decode node. The older path - decoded audio into `context_audio` with the
H3 audio VAE in `audio_vae` - still works and is used when no latent is
wired; it costs one extra lossy VAE round trip per link (see Limitations).
Wire the `trim_frames` output into the Trim node so the duplicated head -
picture and sound together, in sync - comes off before you concatenate.

For **Ref2VA/R2V**, connect the conditioning and latent from the stock
`MiniMaxH3ReferenceToVideo` node exactly the same way. Motion Context preserves
its existing image, video, and audio references, then appends the continuation
audio as the final reference block. This ordering matters: older versions of
this repo replaced `minimax_refs`, which silently dropped the R2V references,
and the layout patch rejected the resulting multi-reference audio setup. Both
paths are now handled inside the Motion Context node; no separate patch script
or ComfyUI-core edit is required.

The Ref2VA multi-reference/audio compatibility
[fix](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771676158206032)
and [six-clip global-ref demo workflow](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771814452793474)
were contributed by **seitanism** in the Banodoco MiniMax H3
seamless-extension thread.
They are included here with attribution; this repo integrates the shared patch
directly so users do not have to run its external patching script.

## Settings and what to pick

**context_length** - how many frames of the previous clip to carry over.
The video VAE only distinguishes certain run lengths, so useful values are
**5, 22, or 39**; anything else snaps down to the nearest. 5 is just barely
fluid, 22 is nearly seamless, 39 is untested. **Use 22.**

**encode_mode** - `video` (default) encodes the whole run in one VAE call
so the motion lives inside the latent. `frames` encodes each frame as a
separate still; it costs twice the rows and left a visible seam in testing.
**Use video.** `frames` remains only for comparison.

**anchor_mode** - `head` (default) pins the frames at the start of the
clip; they come back in the output and the Trim node removes them. `before`
places them at negative time instead so nothing needs trimming - but its
coordinates collide with the text conditioning, which weakens the anchors
and consistently darkens output, failing subtly rather than loudly.
**Use head.** `before` remains only so the failure can be reproduced.

**audio_mode** - `timeline` (default) places the pinned audio on the new
clip's own timeline so the model continues it. `ref` is the stock
placement, which the model *imitates* instead - similar music, not the
same recording, and an audible tick at every join. **Use timeline.**
`ref` remains only for comparison.

**audio_context_length** - how much tail audio to pin, in frames,
independent of the video window. It is end-aligned with the pinned video,
so both always finish at the same instant (the join) and this only controls
how far back the sound reaches. **Use 22** to overlay the video window
exactly; that's the tested config. Longer windows (44, 96) are legal and
land in safe coordinate space, but nobody has rendered one yet.

The Trim node also has `match_tail` (default on). Leave it on: H3 rounds
its audio grid up, so every clip carries ~8 ms more sound than picture, and
without the trim that error grows at every join in a chain.

## The audio story

The first version put pinned audio through H3's reference mechanism, which
is where audio conditioning normally lives. Joins had a small tick - the
audio seemed to briefly speed up and go offbeat. Waveform inspection showed
no splice error; both sides of every join were individually smooth.

Cross-correlating each clip's opening against the previous clip's ending
(the `tests/seam_probe.py` script in this repo) revealed the real problem: the
new clip's audio *resembled* the old clip's - same instruments, same
groove - but never matched it. A cover band, not the same recording. The
model was treating the reference as "a separate clip that sounds like
this," which is exactly what references are for, and exactly wrong for
continuation.

The fix mirrors what already worked for video: the rows the model sees are
identical between the two mechanisms; only their **time coordinates**
differ, and the coordinates are what tell the model "separate clip" versus
"this clip, earlier." So the pinned audio keeps riding the reference
machinery for construction, and its coordinates are rewritten onto the new
clip's own timeline, ending exactly where the pinned video ends. After the
change, measured correlation at the joins went from ~0.45 with incoherent
timing to 0.95+ with a flat, stable offset, and the tick disappeared. The
same measurement across a multi-clip chain shows the offset does **not**
grow from join to join - each clip re-anchors from absolute positions, so
timing errors don't compound.

`tests/seam_probe.py` is included. Point it at the previous clip's audio and the
new clip's **untrimmed** audio and it scores the join:

```
python tests/seam_probe.py clipA.flac clipB_untrimmed.flac --frames 22 --win-ms 100 --search-ms 60
```

## Limitations

**Sound quality degrades down a chain.** This is the big one. Each clip's
audio is generated from the previous clip's *output*, which was generated
from the clip before it, and so on. Like photocopying a photocopy, losses
compound, and (like most lossy audio compression) the top end goes first.
In practice: timing and tempo stay locked, but after several clips the
audio gets noticeably duller and more muffled. Video degrades far less
visibly. Two loss sources stack per link: the model's own regeneration
smoothing, and an extra pass through the audio VAE's encode/decode cycle.
The `context_latent` input eliminates the second one by slicing the pinned
audio straight from the previous clip's latent - wire it and the VAE
round trip is gone. How much of the muffling that removes is newly
measurable, not yet established; the model's own smoothing remains either
way. Whatever remains: treat long chains as territory to listen to
critically, and consider placing chain restarts at natural musical
transitions where a fresh start won't be noticed.

**A small constant audio offset.** Measurement shows each context-generated
clip's audio sits a fixed ~10 ms late. It is constant - it does not grow
down the chain and does not affect tempo - and it is below the threshold
where lip-sync errors become perceptible, but it is real and unfixed.

**Testing breadth.** Joins have been verified clean on two very different
kinds of material: dense beat-driven electronic music (where timing errors
are most audible) and spoken word via the latent path (where nothing masks
a seam and the ear is least forgiving about artifacts).

**One machine, one configuration.** Everything here was verified on a
single Windows machine at one resolution with one sampler. The math is
self-tested at every startup; the perceptual results are one person's
renders.

**ComfyUI's H3 support is young and moving.** The patches depend on the
current shape of ComfyUI's H3 code. They verify those assumptions at
startup and shut down loudly if anything changed, so the failure mode is
"the node refuses to run after an update," not corrupted output.

**Turn Spectrum off.** Step-skipping optimizers like
ComfyUI-Spectrum-MiniMax-H3 forecast how the model's state evolves across
steps. Pinned rows never evolve, which is a degenerate case for the
forecaster. Keep it disabled for these graphs.

**License.** The H3 community license reportedly does not currently cover
the EU, UK, Korea, or the US. Verify independently before building
anything shipping on this.

## Recommended starting point

`context_length 22, encode_mode video, anchor_mode head, audio_mode
timeline, audio_context_length 22`, Trim node wired for both picture and
sound with `match_tail` on, Spectrum off. That is the configuration every
"it works" claim in this README refers to.

## Status and testing

Built and verified against ComfyUI master as of early August 2026, while
H3 support was days old. The math patches self-test against the live
ComfyUI code at every startup, so an upstream change surfaces as a clear
refusal, not a bad render. The repo also ships two standalone test
scripts that run without ComfyUI or a GPU (only numpy needed):

```
python tests/_mock_harness.py       # patch logic against a faithful stock model
python tests/_node_smoke_test.py    # the node end to end, R2V refs + save/load
```

Both should print their checks and finish with a pass line.

## Files

| File | Role |
|---|---|
| `patch_layout.py` | Lifts the first/last-only keyframe restriction; moves pinned audio onto the clip timeline, including after existing R2V refs; keeps everything aligned when references shift the layout. Self-tests at startup. |
| `patch_payload.py` | Lets pinned video and pinned audio coexist (stock code let one overwrite the other). |
| `nodes.py` | The four nodes: Motion Context, Trim, and the latent Save/Load pair. |
| `tests/seam_probe.py` | Measures whether a join's audio is a true continuation, a sound-alike, or drifting. |
| `tests/` | Standalone tests for the patches and the node; run without ComfyUI (numpy only). |

The `example_workflows/` folder contains both the original compact FL2VA demo
and seitanism's six-clip Ref2VA/global-reference chain. See its README for the
additional workflow dependencies.
