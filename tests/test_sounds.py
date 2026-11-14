import io
import wave

from acs import sounds


def test_every_sound_is_a_short_quiet_valid_wav():
    for name, notes in sounds._SOUNDS.items():
        data = sounds._synth(notes)
        with wave.open(io.BytesIO(data)) as w:
            assert w.getnchannels() == 1 and w.getframerate() == 44100
            assert w.getnframes() / 44100 < 1.0, name  # short
            frames = w.readframes(w.getnframes())
        peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 2))
        assert peak < 0.8 * 32767, name  # soft, no clipping
        assert abs(int.from_bytes(frames[:2], "little", signed=True)) < 300  # starts from silence (no click)


def test_play_never_raises():
    sounds.play("nope")
