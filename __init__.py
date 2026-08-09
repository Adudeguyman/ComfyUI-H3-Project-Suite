"""H3 Project Suite.

Clip chaining for MiniMax H3: pin the tail of the previous clip (picture
and sound) so the next clip genuinely continues it.

Registers the nodes without changing ComfyUI's runtime behavior.
The H3 Context node activates both marker-gated patches inline on
first execution, so stock H3 workflows stay stock even with the
suite installed:

  patch_layout   lifts the first/last-only keyframe anchor restriction,
                 moves pinned audio onto the clip's own timeline, and
                 keeps anchor coordinates aligned when refs shift the
                 layout cursor
  patch_payload  stops the refs branch clobbering keyframe cond latents,
                 so pinned video and pinned audio can be used together

If either self-test fails the nodes still load but refuse to run the
affected path, so an upstream ComfyUI change produces a clear message
rather than a silently wrong render.
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

try:
    from .project_nodes import (NODE_CLASS_MAPPINGS as _PN,
                                NODE_DISPLAY_NAME_MAPPINGS as _PD)
    NODE_CLASS_MAPPINGS.update(_PN)
    NODE_DISPLAY_NAME_MAPPINGS.update(_PD)
except Exception:  # torch/folder_paths absent in bare test environments
    import logging as _logging
    _logging.getLogger("h3_suite").exception(
        "h3_suite: project nodes failed to load")

try:
    from . import routes as _routes  # registers /h3_suite/ endpoints
except Exception:
    pass

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS",
           "WEB_DIRECTORY"]
