"""Soft chime sounds synthesized in memory (sine + faint overtone, smooth attack, exponential decay).

No audio files and no extra dependencies; playback is asynchronous via winsound.
"""
from __future__ import annotations

import io
import math
import os
import struct
import tempfile
import wave
import winsound
from pathlib import Path

_RATE = 44100
_cache: dict[str, str] = {}  # name -> path of the cached .wav (PlaySound can't play memory buffers asynchronously)

# name -> list of (frequency Hz, start offset s, duration s, volume 0..1)
_SOUNDS: dict[str, list[tuple[float, float, float, float]]] = {
    "match": [(659.3, 0.00, 0.45, 0.34), (880.0, 0.13, 0.60, 0.34)],  # E5 -> A5, gentle rising chime
    "ok": [(784.0, 0.00, 0.30, 0.26)],  # G5, short and soft
    "fail": [(392.0, 0.00, 0.22, 0.22), (311.1, 0.11, 0.30, 0.22)],  # G4 -> Eb4, soft falling
}


def _synth(notes: list[tuple[float, float, float, float]]) -> bytes:
    total = max(start + dur for _f, start, dur, _v in notes) + 0.05
    n = int(total * _RATE)
    buf = [0.0] * n
    for freq, start, dur, vol in notes:
        i0, length = int(start * _RATE), int(dur * _RATE)
        attack = int(0.012 * _RATE)  # 12 ms fade-in: no click
        for i in range(min(length, n - i0)):
            t = i / _RATE
            env = math.exp(-t * 6.5 / dur) * (min(1.0, i / attack) if attack else 1.0)
            s = math.sin(2 * math.pi * freq * t) + 0.22 * math.sin(2 * math.pi * freq * 2 * t)
            buf[i0 + i] += vol * env * s / 1.22
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in buf))
    return out.getvalue()


def play(name: str) -> None:
    """Play a named chime ('match', 'ok', 'fail') without blocking. Never raises."""
    try:
        path = _cache.get(name)
        if path is None or not os.path.exists(path):
            folder = Path(tempfile.gettempdir()) / "AutoAcceptCS2-sounds"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{name}.wav"
            target.write_bytes(_synth(_SOUNDS[name]))
            path = _cache[name] = str(target)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
