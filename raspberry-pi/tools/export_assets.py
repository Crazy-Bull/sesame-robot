#!/usr/bin/env python3
"""Record the real firmware's poses using a host C++ compiler; extract OLED bytes.

No GPIO or Arduino toolchain is used. Generated JSON is committed, so the Pi
does not need a compiler or the original firmware directory at runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "raspberry-pi/sesame_pi/assets"
POSES = {
    "rest": "runRestPose()", "stand": "runStandPose(1)",
    **{name: "run" + name.title() + "Pose()" for name in
       ("wave", "dance", "swim", "point", "pushup", "bow", "cute", "freaky",
        "worm", "shake", "shrug", "dead", "crab")},
    "forward": "runWalkPose()", "backward": "runWalkBackward()",
    "left": "runTurnLeft()", "right": "runTurnRight()",
}
CONTINUOUS = {"forward", "backward", "left", "right"}
SHIM = '''#pragma once
#include <cstdint>
#include <string>
using String = std::string;
#define F(x) x
struct SerialStub { void println(const char*) {} };
static SerialStub Serial;
'''
RECORDER = r'''
#include <iostream>
#include "movement-sequences.h"
int frameDelay = 100, walkCycles = 1;
String currentCommand;
int checks = 0;
struct Captured {};
void setServoAngle(uint8_t ch, int a) {
  std::cout << "[\"servo\"," << int(ch) << "," << a << "]\n";
}
void setFaceWithMode(const String& name, FaceAnimMode mode) {
  std::cout << "[\"face\",\"" << name << "\"," << int(mode) << "]\n";
}
void setFace(const String& name) { setFaceWithMode(name, FACE_ANIM_LOOP); }
void setFaceMode(FaceAnimMode) { throw std::runtime_error("unsupported face mode event"); }
void delayWithFace(unsigned long ms) { std::cout << "[\"wait\"," << ms << "]\n"; }
void enterIdle() { setFaceWithMode("idle", FACE_ANIM_BOOMERANG); }
bool pressingCheck(String, int) {
  std::cout << "[\"frame\"]\n";
  ++checks;
  if (checks == 2) std::cout << "[\"loop\"]\n";
  if (checks == 10) throw Captured{}; // capture two cycles to verify repetition
  return true;
}
int main() {
CALLS
}
'''


def movement_assets(compiler):
    calls = []
    for name, call in POSES.items():
        calls.append('currentCommand = "%s"; checks = 0; '
                     'std::cout << "[\\\"action\\\",\\\"%s\\\"]\\n"; '
                     'try { %s; } catch (Captured&) {}' % (name, name, call))
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        (temp / "Arduino.h").write_text(SHIM)
        (temp / "capture.cpp").write_text(RECORDER.replace("CALLS", "\n".join(calls)))
        subprocess.run([compiler, "-std=c++11", "-I", str(temp), "-I",
                        str(ROOT / "firmware"), str(temp / "capture.cpp"),
                        "-o", str(temp / "capture")], check=True)
        output = subprocess.check_output([str(temp / "capture")], text=True)
    motions = {}
    events = None
    for line in output.splitlines():
        event = json.loads(line)
        if event[0] == "action":
            name = event[1]
            motions[name] = {"intro": [], "loop": []}
            events = motions[name]["intro"]
        elif event[0] == "loop":
            events = motions[name]["loop"]
        else:
            events.append(event)
    for name in CONTINUOUS:
        events = motions[name]["loop"]
        half = len(events) // 2
        if events[:half] != events[half:] or not events:
            raise ValueError("Firmware gait structure changed: " + name)
        motions[name]["loop"] = events[:half]
    return motions


def face_assets():
    text = (ROOT / "firmware/face-bitmaps.h").read_text()
    names = re.findall(r"\bX\((\w+)\)", text.split("#define X(name)")[0])
    arrays = {}
    for name, body in re.findall(
        r"const unsigned char epd_bitmap_(\w+)\s*\[\s*\]\s*PROGMEM\s*=\s*\{(.*?)\};",
        text, re.S,
    ):
        data = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{2})\b", body))
        if len(data) != 1024:
            raise ValueError("Expected 128x64 bitmap: " + name)
        arrays[name] = data.hex()
    firmware = (ROOT / "firmware/sesame-firmware-main.ino").read_text()
    fps_table = firmware.split("const FaceFpsEntry faceFpsEntries[] = {")[1].split("};")[0]
    fps = dict((name, int(value)) for name, value in
               re.findall(r'\{\s*"(\w+)"\s*,\s*(\d+)\s*\}', fps_table))
    faces = {}
    for name in names:
        # The upstream stand and misspelled defualt symbols are weak and absent.
        # Use the real idle image rather than exporting an empty display.
        source_name = name if name in arrays else "idle"
        frames = [arrays[source_name]]
        for i in range(1, 6):
            if name + "_" + str(i) not in arrays:
                break
            frames.append(arrays[name + "_" + str(i)])
        public_name = "default" if name == "defualt" else name
        faces[public_name] = {"fps": fps.get(public_name, 8), "frames": frames,
                              "source": source_name}
    return faces


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="c++")
    parser.add_argument("--check", action="store_true", help="fail if committed assets are stale")
    args = parser.parse_args()
    sources = ["movement-sequences.h", "face-bitmaps.h", "sesame-firmware-main.ino"]
    outputs = {
        "motions.json": movement_assets(args.compiler),
        "faces.json": face_assets(),
        "sources.json": {name: hashlib.sha256((ROOT / "firmware" / name).read_bytes()).hexdigest()
                         for name in sources},
    }
    DEST.mkdir(parents=True, exist_ok=True)
    for filename, data in outputs.items():
        content = json.dumps(data, indent=2) + "\n"
        path = DEST / filename
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit("Regenerate stale asset: " + filename)
        else:
            path.write_text(content)
    print("Verified" if args.check else "Exported", len(outputs["motions.json"]),
          "movements and", len(outputs["faces.json"]), "faces")


if __name__ == "__main__":
    main()
