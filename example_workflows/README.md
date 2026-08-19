# Example workflows

Two ways in. Both do the same thing at the core — queue, review, approve,
queue again — they differ in how much scaffolding sits around it.

## H3 Project Suite - chained (vanilla).json

ComfyUI's own recommended MiniMax H3 template with the four suite nodes
added and nothing else changed. Start here: it's the smallest complete
example, and if you already know the stock template you'll recognise
everything except the chain.

**Needs:** [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) and
[ComfyMath](https://github.com/evanspearman/ComfyMath) (both inherited
from the stock template, for the resolution and clip-length helpers).

## H3 Project Suite - Prompt Builder AIO.json

The full working setup: image-to-video and reference-to-video paths side
by side, switchable, with prompt construction and media handling from
[Fantastic MiniMax H3 Prompt Builder](https://github.com/Adudeguyman/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder)
and lora stacking and seeds from
[Fantastic Loras](https://github.com/Adudeguyman/comfyui_fantastic-loras).
Group bypassers pick the active path. This is the one to grow into once
the loop makes sense.

**Needs:** both Fantastic packs above, plus
[rgthree-comfy](https://github.com/rgthree/rgthree-comfy) (group bypassers
and Any Switch) and [KJNodes](https://github.com/kijai/ComfyUI-KJNodes)
(Set/Get, attention backend, preview override).

## Using either one

1. Type a project name on the **H3 Project Hub** node.
2. Queue. Clip 1 renders normally — nothing to bypass, no switches.
3. Click **Open project…** on the Hub, watch it, hit **Approve**.
4. Change your prompt and queue again. Clip 2 continues clip 1.

Queue without approving and you get another take of the same clip, which
is what you want while you're still working on a prompt.

## Notes on the wiring

- **H3 Context** sits between the conditioning source and the guider. It
  also reads the clip's empty latent to learn its shape.
- Its **`vae` input stays unwired** — `video_source` is `latent`, and that
  path never encodes anything.
- **H3 Context Trim**'s `fps` must match your render rate (24 for H3). It
  is the second widget; if a `true` ever lands there, audio gets trimmed
  against 1 fps and joins drift.
- **H3 Project Save** takes the *trimmed* images and audio but the
  *untrimmed* sampler latent — the next clip conditions on the full one.
- SaveVideo / VHS Combine are optional. The project writes its own mp4
  either way, so mute them unless you want a second copy.

## Credits

The Ref2VA multi-reference and audio compatibility fix these rely on was
contributed by **seitanism** in the Banodoco MiniMax H3 seamless-extension
thread ([patch](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771676158206032),
[workflow](https://discord.com/channels/1076117621407223829/1535700117452226560/1535771814452793474),
shared 2026-08-08), by way of
[ethanfel's fork](https://github.com/ethanfel/ComfyUI-H3-Motion-Context).
It is built into this pack — do not run the separately posted patch script
on this version.
