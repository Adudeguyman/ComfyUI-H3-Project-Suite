"""The project layer's two graph nodes.

Execution-model reality: the thing that feeds the sampler (context) and the
thing that records its result cannot be one node without a cycle, so the
layer is a source and a sink around the existing chain:

    H3 Project Hub -> H3 Context -> sampler -> decode -> Trim
                                                           -> H3 Project Save

The Hub resolves the manifest into this run's inputs: the approved tail's
latent (video AND audio context now come from it), a chain_active flag that
drives H3 Context's `enabled` passthrough (killing the clip-1 bypass ritual),
and a project handle. Project Save writes the (mp4, safetensors) pair as one
atomic unit named by the manifest and appends the pending entry. Approve /
re-roll / reject live on the manifest via the HTTP routes; the next queue
press re-resolves because IS_CHANGED hashes the manifest.
"""

import datetime
import json
import logging
import os
import tempfile

import folder_paths

from .project import Project, ProjectError, list_projects
from .nodes import _st_load, _st_save, _streams_from_latent

try:
    import torch
except ImportError:  # headless tests stub this
    torch = None

try:
    import av
except ImportError:
    av = None

_LOG = logging.getLogger("h3_suite")

FPS_DEFAULT = 24


def _now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _atomic_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".side_",
                               suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _placeholder_latent():
    """A minimal, obviously-wrong AV latent for the inactive-chain case.

    H3 Context never reads it when enabled is False; if someone forces it
    through anyway the 2-step video / 4-step audio shapes fail loudly in
    the slicing paths rather than rendering something subtly wrong.
    """
    if torch is None:
        raise RuntimeError("h3_suite: torch unavailable")
    return {"samples": [torch.zeros((1, 24, 2, 4, 4)),
                        torch.zeros((1, 32, 2, 4))]}


class H3ProjectHub:
    """Resolve a project into this run's chain inputs."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_name": ("STRING", {
                    "default": "MyProject",
                    "tooltip": "Folder under output/h3_projects/. The whole "
                               "chain - clips, latents, manifest, trash - "
                               "lives inside it."}),
                "create_if_missing": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Create the project on first run. Turn off to "
                               "make a typo fail loudly instead of quietly "
                               "starting a fresh empty project."}),
            },
        }

    RETURN_TYPES = ("H3_PROJECT", "LATENT", "BOOLEAN", "STRING")
    RETURN_NAMES = ("project", "context_latent", "chain_active", "status")
    FUNCTION = "resolve"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("One project per chain: resolves the approved tail's "
                   "latent as context, arms/disarms H3 Context via "
                   "chain_active, and hands Project Save its identity.")

    @classmethod
    def IS_CHANGED(cls, project_name, create_if_missing=True):
        # the widget string is constant while the manifest behind it moves
        # (approve, reject, a finished render). Key the cache on the
        # manifest's identity + mtime so every transition re-resolves.
        try:
            p = Project(folder_paths.get_output_directory(), project_name,
                        create=False)
            return p.mtime_token()
        except Exception:
            return float("NaN")

    def resolve(self, project_name, create_if_missing=True):
        out_dir = folder_paths.get_output_directory()
        p = Project(out_dir, project_name, create=bool(create_if_missing))

        tail_path = p.tail_latent_path()
        if tail_path is not None:
            if _st_load is None:
                raise RuntimeError("h3_suite: safetensors unavailable")
            data = _st_load(tail_path)
            if "video" not in data or "audio" not in data:
                raise ProjectError(
                    "h3_suite: %s is not an H3 AV context latent." % tail_path)
            context = {"samples": [data["video"], data["audio"]]}
            active = True
        else:
            context = _placeholder_latent()
            active = False

        index, take, basename = p.next_save()
        tail = p.chain_tail()
        pend = p.pending()
        bits = ["%d approved" % len(p.approved())]
        if pend is not None:
            bits.append("clip %d take %d PENDING REVIEW"
                        % (pend["index"], pend["take"]))
        bits.append("next render: %s" % basename)
        bits.append("continues %s" % (tail["basename"] if tail else
                                      "nothing (fresh clip 1)"))
        status = " | ".join(bits)
        _LOG.info("h3_suite: project %r: %s", p.name, status)

        handle = {"name": p.name, "output_dir": out_dir}
        return (handle, context, active, status)


def _write_video(path, images, audio, fps, tags=None):
    """Encode post-Trim frames + audio to h264/aac mp4 via PyAV.

    images: [N,H,W,C] float 0..1. audio: ComfyUI AUDIO dict or None.
    """
    if av is None:
        raise RuntimeError(
            "h3_suite: PyAV is not available; cannot write the project "
            "video. `pip install av` into the ComfyUI environment.")
    import numpy as np

    arr = images.cpu().numpy() if hasattr(images, "cpu") else np.asarray(
        images)
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    n, height, width = arr.shape[0], arr.shape[1], arr.shape[2]

    # use_metadata_tags is REQUIRED: without it the mov/mp4 muxer writes
    # only its fixed standard tags (title, comment, ...) and SILENTLY
    # drops custom keys like workflow/prompt, so the file looks fine and
    # carries nothing. faststart keeps the moov atom at the front so the
    # panel can start playing without seeking to the end.
    container = av.open(
        path, mode="w",
        options={"movflags": "+faststart+use_metadata_tags"})
    if tags:
        for k, v in tags.items():
            try:
                container.metadata[k] = v
            except Exception:
                pass
    try:
        vs = container.add_stream("libx264", rate=int(fps))
        vs.width, vs.height = width, height
        vs.pix_fmt = "yuv420p"
        vs.options = {"crf": "17", "preset": "medium"}

        astream = None
        if audio is not None:
            wave = audio["waveform"]
            wav = wave.cpu().numpy() if hasattr(wave, "cpu") else np.asarray(
                wave)
            if wav.ndim == 3:
                wav = wav[0]
            sr = int(audio["sample_rate"])
            ch = int(wav.shape[0])
            layout = "stereo" if ch == 2 else "mono"
            astream = container.add_stream("aac", rate=sr)
            astream.layout = layout

        for i in range(n):
            frame = av.VideoFrame.from_ndarray(arr[i], format="rgb24")
            for pkt in vs.encode(frame):
                container.mux(pkt)
        for pkt in vs.encode():
            container.mux(pkt)

        if astream is not None:
            wav32 = np.ascontiguousarray(wav.astype(np.float32))
            chunk = 1024
            pts = 0
            for s in range(0, wav32.shape[1], chunk):
                seg = wav32[:, s:s + chunk]
                af = av.AudioFrame.from_ndarray(
                    np.ascontiguousarray(seg), format="fltp", layout=layout)
                af.sample_rate = sr
                af.pts = pts
                pts += seg.shape[1]
                for pkt in astream.encode(af):
                    container.mux(pkt)
            for pkt in astream.encode():
                container.mux(pkt)
    finally:
        container.close()


class H3ProjectSave:
    """Record a finished render into the project as one atomic pair."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("H3_PROJECT",),
                "latent": ("LATENT", {
                    "tooltip": "The sampler's output latent - the same one "
                               "wired into the decode nodes. Saved beside "
                               "the video so the next clip can continue "
                               "from it."}),
                "images": ("IMAGE", {
                    "tooltip": "Post-Trim frames. The stored video's tail "
                               "must be the true clip tail."}),
                "fps": ("INT", {"default": FPS_DEFAULT, "min": 1,
                                "max": 120}),
            },
            "optional": {
                "audio": ("AUDIO", {
                    "tooltip": "Post-Trim audio. Optional, but a project "
                               "clip without sound cannot be judged at "
                               "review."}),
            },
            # ComfyUI hands these over for free; they are what makes a clip
            # reusable later instead of just watchable
            "hidden": {"prompt": "PROMPT",
                       "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("basename",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Write this render's mp4 + AV latent into the project's "
                   "clips folder under one manifest-driven name, and mark "
                   "it pending review. Naming, indexing and re-roll "
                   "bookkeeping all come from the manifest - nothing to "
                   "type, nothing to advance.")

    def save(self, project, latent, images, fps, audio=None, prompt=None,
             extra_pnginfo=None):
        if _st_save is None:
            raise RuntimeError("h3_suite: safetensors unavailable")
        p = Project(project["output_dir"], project["name"])
        # resolve the slot at SAVE time from a fresh manifest, not at Hub
        # time: an approve clicked mid-render must not shift this render
        # into the wrong slot silently -- record_render still validates.
        index, take, basename = p.next_save()

        parts = _streams_from_latent(latent)
        if len(parts) < 2:
            raise ValueError(
                "h3_suite: latent has no audio stream; wire the sampler "
                "output of an H3 AV graph.")
        video_lat = parts[0].cpu().contiguous()
        audio_lat = parts[1].cpu().contiguous()

        os.makedirs(p.clips_dir, exist_ok=True)
        video_path = os.path.join(p.clips_dir, basename + ".mp4")
        latent_path = os.path.join(p.clips_dir, basename + ".safetensors")

        # latent first: it is the unreconstructable half. If the video
        # encode then fails, record_render never runs, the manifest never
        # sees the clip, and the stray files are overwritten by the retry.
        # facts worth having without opening anything
        frames = int(images.shape[0])
        meta = {
            "width": int(images.shape[2]),
            "height": int(images.shape[1]),
            "frames": frames,
            "fps": int(fps),
            "duration": round(frames / float(fps), 4),
            "latent_video": list(video_lat.shape),
            "latent_audio": list(audio_lat.shape),
            "saved_at": _now_iso(),
        }
        if audio is not None:
            meta["sample_rate"] = int(audio["sample_rate"])

        workflow = None
        if isinstance(extra_pnginfo, dict):
            workflow = extra_pnginfo.get("workflow")

        # safetensors metadata must be flat str->str
        st_meta = {"format": "h3_motion_context_av_v1",
                   "h3_project": p.name,
                   "h3_clip": basename,
                   "h3_meta": json.dumps(meta, separators=(",", ":"))}
        _st_save({"video": video_lat, "audio": audio_lat}, latent_path,
                 metadata=st_meta)

        # sidecar: the authoritative record, plain JSON next to the pair
        sidecar = {
            "project": p.name, "clip": basename,
            "index": index, "take": take,
            "meta": meta,
            "prompt": prompt,
            "workflow": workflow,
        }
        side_path = os.path.join(p.clips_dir, basename + ".json")
        _atomic_json(side_path, sidecar)

        tags = {
            "title": "%s %s" % (p.name, basename),
            "comment": json.dumps({"project": p.name, "clip": basename,
                                   "meta": meta},
                                  separators=(",", ":")),
        }
        # the full graph goes in the container too when it is small enough
        # to be worth carrying; the sidecar always has it either way
        embedded = []
        for key, obj in (("workflow", workflow), ("prompt", prompt)):
            if obj is None:
                continue
            blob = json.dumps(obj, separators=(",", ":"))
            if len(blob) <= 2 * 1024 * 1024:
                tags[key] = blob
                embedded.append("%s %dKB" % (key, len(blob) // 1024))
            else:
                _LOG.warning(
                    "h3_suite: %s is %d KB, too large to embed; it is in "
                    "the sidecar json", key, len(blob) // 1024)
        if embedded:
            _LOG.info("h3_suite: embedding %s in %s.mp4",
                      ", ".join(embedded), basename)
        _write_video(video_path, images, audio, fps, tags)

        p.record_render(index, take, meta)
        _LOG.info("h3_suite: project %r recorded %s (pending review)",
                  p.name, basename)
        return (basename,)


NODE_CLASS_MAPPINGS = {
    "H3ProjectHub": H3ProjectHub,
    "H3ProjectSave": H3ProjectSave,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ProjectHub": "H3 Project Hub",
    "H3ProjectSave": "H3 Project Save",
}
