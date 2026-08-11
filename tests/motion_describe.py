"""Describe a clip's ending motion as MiniMax prompt vocabulary.

    python motion_describe.py clip_006_take4.mp4

Reads the clip's last second, measures the camera's motion frame to
frame, and prints ready-to-paste prompt lines for the NEXT clip in the
chain, using the camera vocabulary the model was trained on (tracking,
pan, static, shake, with the guide's amplitude and speed expressions).

Why this exists: the pinned frames transfer the previous clip's pixels
but not its intent. The model reads intent from the prompt, and the
prompt knows nothing about what the camera was doing when the last clip
ended. This tool closes that gap - the tail's measured motion becomes
trained vocabulary in the new clip's opening, so the pin and the prompt
agree about the shot instead of the prompt being silent.

Method: global translation between consecutive frames by phase
correlation (numpy FFT, no OpenCV needed). That captures camera-level
motion - pan/tilt/track and shake. It deliberately does not attempt
subject-level motion: describing the subject's action is your job, this
covers the camera's.

Needs PyAV and numpy.
"""

import argparse
import sys

try:
    import av
    import numpy as np
except ImportError:
    print("needs PyAV and numpy: pip install av numpy")
    sys.exit(1)


def read_tail(path, n):
    frames = []
    with av.open(path) as c:
        st = c.streams.video[0]
        fps = float(st.average_rate) if st.average_rate else 24.0
        for f in c.decode(video=0):
            g = f.to_ndarray(format="gray").astype(np.float32)
            frames.append(g)
            if len(frames) > n:
                frames.pop(0)
    return frames, fps


def phase_shift(a, b):
    """Global translation from a to b, in pixels, via phase correlation."""
    win_y = np.hanning(a.shape[0])[:, None]
    win_x = np.hanning(a.shape[1])[None, :]
    fa = np.fft.rfft2(a * win_y * win_x)
    fb = np.fft.rfft2(b * win_y * win_x)
    cross = fa * np.conj(fb)
    denom = np.abs(cross)
    denom[denom < 1e-9] = 1e-9
    corr = np.fft.irfft2(cross / denom, s=a.shape)
    peak = np.unravel_index(np.argmax(corr), corr.shape)
    dy, dx = peak
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    # the CONTENT shifts opposite to the camera: a camera panning right
    # makes the scene drift left in frame. Return CAMERA motion, which is
    # what the prompt vocabulary describes.
    return float(dx), float(dy)


def classify(dxs, dys, w, h, fps):
    """Turn per-frame shifts into guide vocabulary."""
    dx = float(np.median(dxs))
    dy = float(np.median(dys))
    jitter = float(np.median(np.abs(np.array(dxs) - dx)) +
                   np.median(np.abs(np.array(dys) - dy)))
    # speeds in fraction of frame width per second
    vx = dx * fps / w
    vy = dy * fps / h

    mag = max(abs(vx), abs(vy))
    parts = []
    if mag < 0.01:
        parts.append("a static shot")
    else:
        direction = []
        if abs(vx) >= 0.01:
            direction.append("right" if vx > 0 else "left")
        if abs(vy) >= 0.01:
            direction.append("up" if vy < 0 else "down")
        # amplitude classes from the guide: small / moderate / large
        if mag < 0.05:
            amp = "with small amplitude"
        elif mag < 0.18:
            amp = "with moderate amplitude"
        else:
            amp = "with large amplitude"
        speed = "at a slow pace" if mag < 0.05 else \
                ("at a steady pace" if mag < 0.25 else "quickly")
        parts.append("the camera continues moving %s %s, %s"
                     % (" and ".join(direction), amp, speed))
    if jitter > 1.2:
        parts.append("shaking strongly")
    elif jitter > 0.35:
        parts.append("shaking slightly")
    return parts, (vx, vy, jitter)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", help="the previous clip (its ending is measured)")
    ap.add_argument("--seconds", type=float, default=1.0,
                    help="how much of the tail to measure (default 1s)")
    args = ap.parse_args()

    probe, fps = read_tail(args.clip, 2)
    n = max(4, int(round(args.seconds * fps)))
    frames, fps = read_tail(args.clip, n)
    if len(frames) < 4:
        print("clip too short to measure")
        return 1
    h, w = frames[0].shape
    # downscale for speed; shifts scale back up
    step = max(1, min(h, w) // 240)
    small = [f[::step, ::step] for f in frames]
    dxs, dys = [], []
    for a, b in zip(small, small[1:]):
        dx, dy = phase_shift(a, b)
        dxs.append(dx * step)
        dys.append(dy * step)

    parts, (vx, vy, jitter) = classify(dxs, dys, w, h, fps)

    print("measured over the last %.1fs (%d frames):" % (args.seconds,
                                                         len(frames)))
    print("   camera motion %+.3f frame-widths/s horizontal, %+.3f "
          "vertical, jitter %.2f px" % (vx, vy, jitter))
    print()
    print("paste into the NEXT clip's opening (after your style line):")
    print()
    print("   The shot continues uninterrupted, %s, the motion already "
          "underway carrying over from the previous moment."
          % ", ".join(parts))
    print()
    print("Then describe the subject's continuing action yourself - the "
          "camera is measurable, the performance is yours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
