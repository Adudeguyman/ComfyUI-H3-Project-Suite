# H3 Project Suite

**Make long videos with MiniMax H3, one clip at a time, without the joins falling apart.**

H3 makes great five-to-fifteen second clips. Stringing them into something longer is where it gets painful: the usual trick is to take the last frame of one clip and use it as the first frame of the next, which throws away everything except a single still. Motion stops and restarts. The soundtrack cuts out and something vaguely similar starts up. And because every join converts the video out of the model's format and back in again, each one costs a little quality that the next join then builds on.

This suite fixes that, then wraps a project manager around it so you aren't hand-managing files between every clip.

The short version of how: in its default *latent* mode, each clip is handed to the next one in the model's own internal format rather than as pictures and sound. There's no decoding between clips, so the handoff doesn't cost you a conversion the way traditional extension methods do. It isn't magic — the model still does what the model does — but the quality loss that comes from the plumbing is largely removed, which should let you go further before a chain starts looking tired. You never have to think about this; it's just the default.

---

## What you get

**Clips that continue instead of restarting.** The next clip is handed roughly a second of what came before rather than one frozen frame, so motion carries its direction and speed through a join, and sound carries on — the same music playing through, the same voice mid-sentence — rather than a soundalike starting up. How well any individual join lands still depends on your prompt and the model's mood, but it has something real to continue from.

**Minimal quality loss between clips.** In latent mode there's no decoding between clips — the handoff never leaves the model's own format — so there's less quality loss than with traditional extension methods, where every join costs another conversion. Some variation between clips is still normal; this is a generative model. But the losses that come from the plumbing rather than the model are largely off the table.

**References that survive.** If you're using Ref2VA with a voice reference and character images, they keep working on every clip, not just the first one.

**A project, not a pile of files.** Every chain lives in one folder with its own clips, its own history, and a record of what continues from what. No filename schemes, no index numbers to keep straight.

**A review screen.** After each render, watch the new clip *in the context of everything before it* on a scrubbable timeline, then approve it or throw it away. Approving is what tells the next render where to continue from.

**Takes.** Don't like a clip? Change your prompt and render again. Both versions are kept, and you pick between them by watching, not by guessing from filenames.

**Branching.** Fifteen clips in and you want to try a different direction from clip six? Branch there into a brand new project. The original is left completely alone — and you can even branch from a take you never approved.

**Export.** One button stitches your approved clips into a single video. It never overwrites; it suggests the next free filename.

---

## Install

1. Put the folder in `ComfyUI/custom_nodes/`.
2. Restart ComfyUI.
3. Hard-refresh your browser (Ctrl+Shift+R).

You need `ffmpeg` on your system for the export button. Levelling a join and measuring drift also need `av` and `numpy`, which most ComfyUI installs already have — if yours doesn't, the rest of the pack works fine and only that one feature reports a missing dependency.

**Important:** if you have the original *ComfyUI-H3-Motion-Context* pack installed, remove or disable it. The two can't run at the same time — this one detects the conflict and refuses to run rather than produce a bad render.

---

## The four nodes

You wire these up once, then never touch them again.

| Node | What it does |
| --- | --- |
| **H3 Project Hub** | Your control panel. Pick a project, review clips, approve, branch, export. |
| **H3 Context** | Does the actual joining. Set once, forget. |
| **H3 Context Trim** | Removes the overlap at the start of each new clip. Set once, forget. |
| **H3 Project Save** | Saves each finished clip into the project. |

Three connections do the whole job:

```
H3 Project Hub ──context_latent──▶ H3 Context ──conditioning──▶ your guider
               ──chain_active────▶ H3 Context ──trim_frames───▶ Trim
               ──project─────────▶ H3 Project Save
```

Then the normal path: sampler ➜ decode ➜ **Trim** ➜ **Project Save**.

H3 Context's `latent` output goes to your sampler's `latent_image`, in place of the wire from the MiniMax node. That's what lets the **seed head** setting work; both example workflows are already wired this way.

---

## How you actually use it

**1. Name a project.** Type a name on the Hub node, or hit *New* in the panel. That's the whole setup.

**2. Queue.** Clip one renders like any normal H3 job. Nothing to bypass, no switches to flip — the Hub knows this is the first clip and stays out of the way.

**3. Review.** Click **Open project…** on the Hub node. Your clip is sitting there, ready to play.

**4. Approve.** If you like it, hit **Approve**. That makes it the thing the next clip continues from.

**5. Change your prompt and queue again.** Clip two renders, continuing clip one. It appears in the panel with the playhead parked just before the join, so the first thing you see is whether the transition works.

**6. Repeat.** That's the loop: queue → watch → approve → queue.

If you queue *without* approving, you get another take of the same clip instead of moving forward — exactly what you want while you're still fiddling with a prompt. A dropdown lets you switch between takes and watch each one.

---

## The review panel

Everything lives in one screen, opened from the Hub node.

**Timeline** plays your whole chain as one continuous video, with a scrub bar divided into clips — green for approved, amber for the one awaiting your decision. Drag anywhere to jump. **⏮ Jump to the join** snaps you back to the moment before the newest clip starts, which is usually the only part you need to see.

Under the transport it names what you're watching: `clip 7 · take 9/11` — take nine is the one in the chain, out of eleven that exist. It also says `pending` for a clip you haven't decided on yet, and `levelled` when that join is set to be corrected on export. Hovering gives the filename and which clip it continues from.

**Single clip** mode plays one clip on its own when you want a closer look.

**The rail** down the right side lists every clip with a thumbnail. Click one to jump there.

**Approve / Reject** decide the newest clip's fate. Rejected clips go to a trash folder inside the project, so a hasty click isn't fatal.

**Reopen** lets you go back and redo an earlier clip. It tells you exactly which later clips get dropped before you commit, since everything after it was built on it.

**Branch from clip N…** opens a second screen showing what the new chain would look like, ending on whichever version of that clip you choose. Watch it, name it, create it. Your original project doesn't change at all.

**Export master** stitches your approved clips into one file. **Export + pending** includes the clip you haven't approved yet — the best way to judge a join, since the preview player has a tiny hitch between clips that a real export doesn't.

---

## Housekeeping

Extra takes add up: each one is a full video plus the data the next clip needs. The panel footer always tells you where the space is going.

```
6 approved · 30.0s total · 2.1 GB chain, 840 MB in 6 alternate takes
```

**Clean up takes** moves every alternate take into the project's trash. Your chain isn't touched. Takes in the trash can still be branched from, so this is safe to do whenever the folder gets messy.

**Purge trash** is the permanent delete. It tells you how much it's about to remove first. After that, those takes are gone for good.

---

## Measuring drift

Each clip is built on the previous clip's output, so small changes compound: exposure wanders, texture softens. **Measure drift** in the Hub samples every clip in the chain and reports how brightness, contrast, sharpness and colour move from the first to the last, as a total and as a rate per clip, with a bar per clip for whichever moved most.

These numbers also move when the content changes — a clip that cuts to a dark interior is genuinely darker. Read the trend across a run of clips in one continuous scene, not any single value.

If the trend is steep, the cheapest fix isn't a setting: a deliberate cut to a new angle re-derives the look from your references and prompt instead of inheriting it, which resets most of the accumulation.

---

## Levelling a join

Sometimes a clip opens slightly brighter than the one before it and settles back over a second or so. It happens where the new clip stops being held to the old one and relaxes toward its own exposure.

**Level-match join** (next to Reopen when you're on a clip) measures that step and tells you what it found before you commit — how big the step is, and how long it takes to settle. Turn it on and the export corrects that clip's opening so it meets the previous one, fading the correction out as the clip settles.

It's per join, because joins differ: one may need it and the next may be fine. Your clips are never modified — only the exported master. An export containing a corrected join is re-encoded rather than copied, so it takes longer than a plain one.

---

## Settings worth knowing

Almost everything can be left alone. Three are worth understanding:

**Context length (22)** — how much of the previous clip gets handed over. It's a dropdown of the only values that exist: 1, 5, 22, 39 and 56. Bigger means a smoother join but less new footage per render — at 56, nearly two and a half seconds of the new clip re-tread the old one. 22 (just under a second) is a good default, and it's worth changing per clip: a slow push-in benefits from 39 or 56 where a fast cut doesn't.

**Video source (latent)** — the default, and the mode that skips the decoding step between clips as described at the top. `frames` is the older way of doing it, kept for compatibility with hand-built graphs.

**fps (24)** — must match your video's frame rate. H3 runs at 24, so leave it unless you know you've changed something.

**Seed head (on)** — normally the carried-over frames are given to the model as something to *agree with*: it generates the new clip from scratch and is steered toward matching them. With this on, those frames are also written directly into the clip's starting point and held there while it renders, so the model builds forward from the previous clip's actual content instead of from noise that merely resembles it.

This needs the sampler's latent to come from **H3 Context's `latent` output** rather than straight from the MiniMax node. Both example workflows are wired that way. If you built your graph by hand and the sampler still takes its latent from the MiniMax node, this setting does nothing — no harm, just no effect.

If a join shows a texture or quality change about a second in, that's the point where the held frames end. **Head hold (1.0)** controls how firmly they're held; try 0.85, or switch seed head off to compare.

---

## Things to know before you start

**Pick your resolution first.** A chain is locked to the size of its first clip. Want a different aspect ratio? Start a new project.

**Approve is what moves you forward.** Nothing advances until you approve. That's on purpose: a clip you haven't looked at should never become the foundation for the next five.

**Your projects survive everything.** Restart ComfyUI, close the browser, load a different workflow — the project lives on disk, not in the graph. Come back tomorrow and pick up where you left off.

**Clips remember how they were made.** Each one is saved alongside the workflow and prompt that produced it, so you can drop an old clip back into ComfyUI and get its settings back.

---

## Where everything lives

```
ComfyUI/output/h3_projects/YourProject/
├── project.json          the chain's history
├── clips/                your clips, plus the latents that link them
│                         and a .json per take holding the prompt and
│                         workflow that made it
├── .trash/               rejected and cleaned-up takes
└── YourProject_master.mp4
```

Copy that folder to another machine and carry on there.

---

## Credits

Built on [NikoDemon80's ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context), which worked out how to make H3 continue clips at all. The multi-reference fix that keeps voices and character images alive across a chain came from [ethanfel's fork](https://github.com/ethanfel/ComfyUI-H3-Motion-Context), contributed by seitanism.

Everything talks to ComfyUI from the outside — no ComfyUI files are modified. If a future update changes something this relies on, the pack notices and stops rather than quietly producing bad renders.

Curious how the joining actually works, or why audio was the hard part? That's in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).
