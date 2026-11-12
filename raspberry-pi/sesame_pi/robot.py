"""One motion worker; cancellation is serialized with every servo write."""
import json
import logging
from pathlib import Path
import threading
import time

from .config import SERVO_NAMES, SERVO_MODEL, SERVO_FREQUENCY_HZ

log = logging.getLogger(__name__)
CONTINUOUS = frozenset(("forward", "backward", "left", "right"))


class Robot:
    def __init__(self, config, servos, faces, simulation=True):
        self.config, self.servos, self.faces = config, servos, faces
        self.simulation = simulation
        self.motions = json.loads((Path(__file__).parent / "assets/motions.json").read_text())
        self.cv = threading.Condition(threading.RLock())
        self.generation = 0
        self.pending = None
        self.current_command = ""
        self.face_override = None
        self.armed = simulation
        self.estopped = False
        self.closed = False
        self.fault = None
        self.stop_reason = "startup"
        self.last_heartbeat = time.monotonic()
        self.positions = {name: None for name in SERVO_NAMES}
        self.frame_delay_ms = config.frame_delay_ms
        self.motor_delay_ms = config.motor_delay_ms
        self.threads = [
            threading.Thread(target=self._worker, name="sesame-motion", daemon=True),
            threading.Thread(target=self._watchdog, name="sesame-watchdog", daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    @property
    def pins(self):
        return [servo.gpio for servo in self.config.servos]

    def _cancel_locked(self, reason):
        self.generation += 1
        self.pending = None
        self.current_command = ""
        self.face_override = None
        self.stop_reason = reason
        self.cv.notify_all()

    def _release_locked(self):
        try:
            self.servos.release(self.pins)
        except Exception as exc:
            self.fault = str(exc)
            log.exception("Could not disable every servo output")
        finally:
            self.positions = {name: None for name in SERVO_NAMES}

    def _fail_locked(self, exc):
        self._cancel_locked("fault")
        self.armed = False
        self.fault = str(exc)
        log.error("Servo controller fault: %s", exc)
        self._release_locked()

    def command(self, command=None, face=None):
        # Validate the whole request before changing either face or motion.
        if command is None and face is None:
            raise ValueError("Provide command or face")
        if command is not None and (not isinstance(command, str) or
                command not in set(self.motions) | {"stop", "estop", "arm", "disarm"}):
            raise ValueError("Unknown command")
        if face is not None and (not isinstance(face, str) or face not in self.faces.assets):
            raise ValueError("Unknown face")
        with self.cv:
            if self.closed:
                raise RuntimeError("Robot is shut down")
            if command == "arm":
                if self.fault:
                    raise RuntimeError("Restart after resolving the controller fault: " + self.fault)
                if not self.simulation and not all(s.calibrated for s in self.config.servos):
                    raise RuntimeError("Calibrate all eight servos before arming hardware")
                self.armed, self.estopped = True, False
            elif command in ("estop", "disarm"):
                self._cancel_locked(command)
                self.armed = False
                self.estopped = command == "estop"
                self._release_locked()
            elif command == "stop":
                # Hold the most recent targets; never initiate a new stand pose.
                self._cancel_locked("stop")
            elif command is not None:
                if not self.armed:
                    raise RuntimeError("Robot is disarmed; arm it before requesting movement")
                self.last_heartbeat = time.monotonic()
                if command not in CONTINUOUS or command != self.current_command:
                    self._cancel_locked("")
                    self.current_command = command
                    self.face_override = face
                    self.pending = (self.generation, command)
                    self.cv.notify_all()
                elif face is not None:
                    self.face_override = face
            if face is not None:
                self.faces.set(face)

    def heartbeat(self):
        with self.cv:
            if self.current_command in CONTINUOUS:
                self.last_heartbeat = time.monotonic()

    def status(self):
        with self.cv:
            return {
                "currentCommand": self.current_command,
                "currentFace": self.faces.current(),
                "simulation": self.simulation,
                "servoModel": SERVO_MODEL,
                "servoFrequencyHz": SERVO_FREQUENCY_HZ,
                "armed": self.armed,
                "emergencyStopped": self.estopped,
                "fault": self.fault,
                "stopReason": self.stop_reason,
                "servos": {name: dict(value) if value is not None else None
                           for name, value in self.positions.items()},
                "commands": list(self.motions),
                "faces": list(self.faces.assets),
                "commandTimeoutSeconds": self.config.command_timeout_s,
            }

    def settings(self):
        with self.cv:
            return {"frameDelay": self.frame_delay_ms, "motorCurrentDelay": self.motor_delay_ms}

    def set_settings(self, frame_delay_ms, motor_delay_ms):
        from .config import number

        number(frame_delay_ms, "frameDelay", 10, 2000)
        number(motor_delay_ms, "motorCurrentDelay", 0, 200)
        with self.cv:
            self.frame_delay_ms, self.motor_delay_ms = frame_delay_ms, motor_delay_ms

    def _valid_locked(self, generation):
        return not self.closed and self.armed and self.generation == generation

    def _wait(self, generation, milliseconds):
        deadline = time.monotonic() + milliseconds / 1000
        with self.cv:
            while self._valid_locked(generation):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                self.cv.wait(remaining)
            return False

    def _play(self, generation, events):
        for event in events:
            delay = 0
            with self.cv:
                if not self._valid_locked(generation):
                    return False
                kind = event[0]
                if kind == "servo":
                    index, requested = event[1:]
                    servo = self.config.servos[index]
                    angle, pulse, limited = servo.target(requested)
                    self.servos.write(servo.gpio, pulse)
                    self.positions[SERVO_NAMES[index]] = {
                        "requestedAngle": requested, "physicalAngle": angle,
                        "pulseUs": pulse, "limited": limited,
                    }
                    delay = self.motor_delay_ms
                elif kind == "face":
                    if self.face_override is None:
                        self.faces.set(event[1], event[2])
                elif kind == "frame":
                    delay = self.frame_delay_ms
                elif kind == "wait":
                    delay = event[1]
                else:
                    raise RuntimeError("Invalid movement event: " + str(event))
            if delay and not self._wait(generation, delay):
                return False
        return True

    def _worker(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, command = self.pending
                self.pending = None
            try:
                motion = self.motions[command]
                completed = self._play(generation, motion["intro"])
                if command in CONTINUOUS:
                    while completed:
                        completed = self._play(generation, motion["loop"])
                with self.cv:
                    if completed and self._valid_locked(generation):
                        self.current_command = ""
                        self.stop_reason = "completed"
            except Exception as exc:
                with self.cv:
                    self._fail_locked(exc)

    def _watchdog(self):
        with self.cv:
            while not self.closed:
                if (self.current_command in CONTINUOUS and
                        time.monotonic() - self.last_heartbeat > self.config.command_timeout_s):
                    self._cancel_locked("heartbeat-timeout")
                self.cv.wait(0.025)

    def close(self):
        with self.cv:
            if self.closed:
                return
            self.closed = True
            self.armed = False
            self._cancel_locked("shutdown")
            self._release_locked()
        for thread in self.threads:
            thread.join(timeout=2)
        self.servos.close()
