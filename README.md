# H3 Project Suite

**Make long videos with MiniMax H3, one clip at a time, without the joins falling apart.**

H3 makes great five-to-fifteen second clips. Stringing them into something longer is where it gets painful: the usual trick is to take the last frame of one clip and use it as the first frame of the next, which throws away everything except a single still. Motion stops and restarts. The soundtrack cuts out and something vaguely similar starts up. Colours drift a little further with every join.

This suite fixes that, then wraps a project manager around it so you aren't hand-managing files between every clip.

The short version of how: each clip is handed to the next one in the model's own internal format rather than as pictures and sound. Nothing is decoded, re-compressed or converted in between, so nothing degrades along the way — the tenth join looks and sounds as clean as the first. You never have to think about this. It's just the default.

---

## What you get

**Clips that actually continue.** Motion keeps its direction and speed through a join. Sound genuinely continues — the same music playing on, the same voice mid-sentence — instead of a soundalike starting over.

**No colour drift.** Colours stay put no matter how long the chain gets.

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

You need `ffmpeg` on your system for the export button. Everything else ships with ComfyUI.

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
H3 Project Hub ──context_latent──▶ H3 Context ──▶ your sampler
               ──chain_active────▶ H3 Context
               ──project─────────▶ H3 Project Save
```

Then the normal path: sampler ➜ decode ➜ **Trim** ➜ **Project Save**.

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

## Settings worth knowing

Almost everything can be left alone. Three are worth understanding:

**Context length (22)** — how much of the previous clip gets handed over. Bigger means a smoother join but less freedom for the new clip to do something different. 22 frames (just under a second) is a good default. The values that work are 5, 22 and 39.

**Video source (latent)** — leave this on `latent`. That's the mode that avoids the quality loss described at the top. `frames` is the older way, kept for compatibility.

**fps (24)** — must match your video's frame rate. H3 runs at 24, so leave it unless you know you've changed something.

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
├── clips/                your clips, plus the data that links them
├── .trash/               rejected and cleaned-up takes
└── YourProject_master.mp4
```

Copy that folder to another machine and carry on there.

---

## Credits

Built on [NikoDemon80's ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context), which worked out how to make H3 continue clips at all. The multi-reference fix that keeps voices and character images alive across a chain came from [ethanfel's fork](https://github.com/ethanfel/ComfyUI-H3-Motion-Context), contributed by seitanism.

Everything talks to ComfyUI from the outside — no ComfyUI files are modified. If a future update changes something this relies on, the pack notices and stops rather than quietly producing bad renders.

Curious how the joining actually works, or why audio was the hard part? That's in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).
