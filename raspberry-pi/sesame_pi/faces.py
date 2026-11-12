"""Original artwork for browser previews only; no physical display is required."""
import json
from pathlib import Path
import threading
import time

ASSETS = Path(__file__).parent / "assets"


class Faces:
    def __init__(self):
        self.assets = json.loads((ASSETS / "faces.json").read_text())
        self.lock = threading.RLock()
        self.name = "default"
        self.mode = 0
        self.started = time.monotonic()

    def set(self, name, mode=0):
        if name not in self.assets:
            raise ValueError("Unknown face: " + str(name))
        with self.lock:
            if name != self.name or mode != self.mode:
                self.name, self.mode = name, mode
                self.started = time.monotonic()

    def current(self):
        with self.lock:
            return self.name

    def frame(self, now=None):
        with self.lock:
            face = self.assets[self.name]
            count = len(face["frames"])
            tick = int(max(0, (now if now is not None else time.monotonic()) - self.started)
                       * face["fps"])
            if self.mode == 1:
                index = min(tick, count - 1)
            elif self.mode == 2 and count > 1:
                phase = tick % (2 * count - 2)
                index = phase if phase < count else 2 * count - 2 - phase
            else:
                index = tick % count
            return bytes.fromhex(face["frames"][index])
