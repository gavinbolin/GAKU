"""Offline dependency smoke: imports alone did not catch PyAV 19 breakage."""
from pathlib import Path
import math
import struct
import tempfile
import unittest
import wave

class EnvironmentTests(unittest.TestCase):
    def test_gradio_and_whisper_import_together(self):
        import gradio
        import faster_whisper
        import ctranslate2
        self.assertTrue(gradio.__version__)
        self.assertTrue(faster_whisper.__version__)
        self.assertTrue(ctranslate2.__version__)

    def test_whisper_can_decode_audio(self):
        from faster_whisper.audio import decode_audio
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tone.wav"
            with wave.open(str(p), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"".join(struct.pack("<h", int(5000*math.sin(i*2*math.pi*220/16000))) for i in range(4000)))
            audio = decode_audio(str(p))
            self.assertGreater(len(audio), 3900)

if __name__ == "__main__":
    unittest.main()
