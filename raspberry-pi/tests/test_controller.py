import json
import math
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sesame_pi.config import Config, ServoConfig, SERVO_NAMES
from sesame_pi.drivers import SimulatedServos, PigpioServos
from sesame_pi.faces import Faces
from sesame_pi.robot import Robot, CONTINUOUS
from sesame_pi.web import create_app


def wait_until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for controller state")


class RecordingServos(SimulatedServos):
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, gpio, pulse):
        super().write(gpio, pulse)
        self.writes.append((gpio, pulse))


class CalibrationTests(unittest.TestCase):
    def test_conversion_direction_trim_and_limits(self):
        servo = ServoConfig(17, min_pulse_us=600, max_pulse_us=2400,
                            min_angle=20, max_angle=160, offset=10, reverse=True)
        self.assertEqual(servo.target(90), (100, 1600, False))
        self.assertEqual(servo.target(0), (160, 2200, True))
        self.assertEqual(servo.target(180), (20, 800, True))

    def test_invalid_values_and_shared_pins(self):
        for angle in (True, "90", math.nan, math.inf, -1, 181):
            with self.subTest(angle=angle), self.assertRaises(ValueError):
                ServoConfig(17).target(angle)
        for values in ({"gpio": 2}, {"gpio": 3}, {"gpio": True},
                       {"gpio": 17, "min_pulse_us": 400},
                       {"gpio": 17, "max_pulse_us": 2900},
                       {"gpio": 17, "neutral_pulse_us": 2000},
                       {"gpio": 17, "neutral_pulse_us": math.nan},
                       {"gpio": 17, "reverse": "false"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ServoConfig(**values)
        with self.assertRaises(ValueError):
            Config(tuple(ServoConfig(17) for _ in range(8)))

    def test_mg90s_default_pulses_and_asymmetric_neutral(self):
        servo = ServoConfig(17)
        self.assertEqual([servo.target(angle)[1] for angle in (0,90,180)], [1000,1500,2000])
        asymmetric = ServoConfig(17, min_pulse_us=700, neutral_pulse_us=1520, max_pulse_us=2100)
        self.assertEqual([asymmetric.target(angle)[1] for angle in (0,45,90,135,180)],
                         [700,1110,1520,1810,2100])

    def test_example_configuration_is_uncalibrated(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "config.example.json")
        self.assertEqual(len(config.servos), 8)
        self.assertFalse(any(servo.calibrated for servo in config.servos))
        self.assertTrue(all(servo.neutral_pulse_us == 1500 for servo in config.servos))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.driver = RecordingServos()
        base = Config.load()
        self.config = Config(base.servos, frame_delay_ms=10, motor_delay_ms=0,
                             command_timeout_s=0.2)
        self.robot = Robot(self.config, self.driver, Faces())
        self.addCleanup(self.robot.close)
        self.client = create_app(self.robot).test_client()

    def test_exact_rest_and_stand_positions(self):
        for command, angles in (("rest", [90]*8),
                                ("stand", [135, 45, 45, 135, 0, 180, 0, 180])):
            self.robot.command(command)
            wait_until(lambda: self.robot.status()["currentCommand"] == "")
            targets = self.robot.status()["servos"]
            self.assertEqual([targets[name]["requestedAngle"] for name in SERVO_NAMES], angles)

    def test_every_exported_pose_runs_to_completion(self):
        def no_wait(generation, milliseconds):
            with self.robot.cv:
                return self.robot._valid_locked(generation)

        self.robot._wait = no_wait
        for command in set(self.robot.motions) - CONTINUOUS:
            with self.subTest(command=command):
                self.robot.command(command)
                wait_until(lambda: self.robot.status()["stopReason"] == "completed")
                self.assertIsNone(self.robot.status()["fault"])
                self.assertTrue(all(value is not None for value in self.robot.positions.values()))

    def test_stop_interrupts_pose_wait_without_late_servo_writes(self):
        self.robot.command("wave")
        wait_until(lambda: len(self.driver.writes) >= 8)
        started = time.monotonic()
        self.robot.command("stop")
        count = len(self.driver.writes)
        time.sleep(0.25)  # spans the next scheduled pose writes
        self.assertEqual(len(self.driver.writes), count)
        self.assertEqual(self.robot.status()["stopReason"], "stop")
        self.assertLess(time.monotonic() - started, 0.35)

    def test_each_direction_times_out_and_requires_a_new_command(self):
        for direction in CONTINUOUS:
            self.robot.command(direction)
            wait_until(lambda: self.robot.status()["stopReason"] == "heartbeat-timeout")
            count = len(self.driver.writes)
            self.robot.heartbeat()
            time.sleep(0.03)
            self.assertEqual(len(self.driver.writes), count)
            self.assertEqual(self.robot.status()["currentCommand"], "")

    def test_heartbeats_keep_one_gait_running_without_restarting_intro(self):
        self.robot.command("forward")
        for _ in range(5):
            time.sleep(0.07)
            self.robot.heartbeat()
        self.assertEqual(self.robot.status()["currentCommand"], "forward")
        # The intro has one R3=143 write; cycle boundaries contain another.
        # Gait body must have run, not merely repeated intro.
        pulses = [pulse for gpio, pulse in self.driver.writes if gpio == self.config.servos[0].gpio]
        self.assertIn(self.config.servos[0].target(170)[1], pulses)
        self.assertIn(self.config.servos[0].target(102)[1], pulses)

    def test_replacement_command_cancels_wave(self):
        self.robot.command("wave")
        wait_until(lambda: len(self.driver.writes) >= 8)
        self.robot.command("rest")
        wait_until(lambda: self.robot.status()["stopReason"] == "completed")
        count = len(self.driver.writes)
        time.sleep(0.25)
        self.assertEqual(len(self.driver.writes), count)
        self.assertTrue(all(value == 1500 for value in self.driver.pulses.values()))

    def test_estop_releases_every_output_and_latches_disarmed(self):
        self.robot.command("forward")
        wait_until(lambda: bool(self.driver.writes))
        self.robot.command("estop")
        self.assertTrue(all(pulse == 0 for pulse in self.driver.pulses.values()))
        self.assertTrue(self.robot.status()["emergencyStopped"])
        with self.assertRaises(RuntimeError):
            self.robot.command("stand")
        self.robot.command("arm")
        self.robot.command("rest")
        wait_until(lambda: self.robot.status()["stopReason"] == "completed")

    def test_hardware_mode_requires_calibration_and_explicit_arm(self):
        hardware = Robot(self.config, RecordingServos(), Faces(), simulation=False)
        self.addCleanup(hardware.close)
        self.assertFalse(hardware.status()["armed"])
        with self.assertRaises(RuntimeError):
            hardware.command("rest")
        with self.assertRaises(RuntimeError):
            hardware.command("arm")

    def test_calibrated_hardware_arm_does_not_move_until_commanded(self):
        from dataclasses import replace

        config = replace(self.config, servos=tuple(replace(s, calibrated=True) for s in self.config.servos))
        driver = RecordingServos()
        robot = Robot(config, driver, Faces(), simulation=False)
        self.addCleanup(robot.close)
        robot.command("arm")
        self.assertEqual(driver.writes, [])
        robot.command("rest")
        wait_until(lambda: robot.status()["stopReason"] == "completed")
        self.assertEqual(len(driver.writes), 8)

    def test_hardware_write_fault_disarms_and_clears_other_channels(self):
        original = self.driver.write

        def fail_active(gpio, pulse):
            if pulse:
                raise RuntimeError("disconnected servo backend")
            original(gpio, pulse)

        self.driver.write = fail_active
        self.robot.command("stand")
        wait_until(lambda: self.robot.status()["fault"] is not None)
        self.assertFalse(self.robot.status()["armed"])
        self.assertEqual(set(self.driver.pulses.values()), {0})
        with self.assertRaises(RuntimeError):
            self.robot.command("arm")

    def test_invalid_combined_request_does_not_change_face(self):
        response = self.client.post("/api/command", json={"command": "invalid", "face": "happy"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.robot.status()["currentFace"], "default")

    def test_json_validation_methods_and_cross_origin(self):
        for body in ({}, [], {"command": None}, {"command": ["rest"]},
                     {"face": 1}, {"command": "rest", "extra": True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/command", json=body).status_code, 400)
        self.assertEqual(self.client.post("/api/command", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get("/api/command").status_code, 405)
        self.assertEqual(self.client.post("/api/command", json={"command": "rest"},
                                        headers={"Origin": "http://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/missing").status_code, 404)

    def test_face_override_preview_and_settings(self):
        status = self.client.get('/api/status').get_json()
        self.assertEqual(status['servoModel'], 'MG90S')
        self.assertEqual(status['servoFrequencyHz'], 50)
        self.assertEqual(self.client.post("/api/command", json={"command": "rest", "face": "happy"}).status_code, 200)
        wait_until(lambda: self.robot.status()["stopReason"] == "completed")
        self.assertEqual(self.robot.status()["currentFace"], "happy")
        response = self.client.get("/api/face")
        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.data.startswith(b"\x89PNG"))
        self.assertEqual(self.client.post("/api/settings", json={"frameDelay": 25}).status_code, 200)
        self.assertEqual(self.robot.settings()["frameDelay"], 25)
        self.assertEqual(self.client.post("/api/settings", json={"frameDelay": math.nan}).status_code, 400)

    def test_shutdown_releases_outputs_and_joins_workers(self):
        self.robot.command("forward")
        wait_until(lambda: bool(self.driver.writes))
        self.robot.close()
        self.assertTrue(all(value == 0 for value in self.driver.pulses.values()))
        self.assertFalse(any(t.is_alive() for t in self.robot.threads))
        with self.assertRaises(RuntimeError):
            self.robot.command("arm")



class AssetTests(unittest.TestCase):
    def test_all_events_and_bitmap_sizes(self):
        assets = Path(__file__).resolve().parents[1] / "sesame_pi/assets"
        motions = json.loads((assets / "motions.json").read_text())
        faces = json.loads((assets / "faces.json").read_text())
        self.assertEqual(len(motions), 19)
        for name, motion in motions.items():
            self.assertEqual(bool(motion["loop"]), name in CONTINUOUS)
            for event in motion["intro"] + motion["loop"]:
                if event[0] == "servo":
                    self.assertIn(event[1], range(8))
                    self.assertTrue(0 <= event[2] <= 180)
                elif event[0] == "face":
                    self.assertIn(event[1], faces)
                else:
                    self.assertIn(event[0], ("wait", "frame"))
        for face in faces.values():
            for frame in face["frames"]:
                self.assertEqual(len(bytes.fromhex(frame)), 1024)

    def test_face_animation_modes(self):
        face = Faces()
        for mode, ticks in ((0, [0,1,2,0,1]), (1, [0,1,2,2,2]), (2, [0,1,2,1,0])):
            face.set("rest", mode)
            face.started = 0
            frames = face.assets["rest"]["frames"]
            for tick, expected in enumerate(ticks):
                self.assertEqual(face.frame(tick + 0.01), bytes.fromhex(frames[expected]))

    def test_pigpio_adapter_writes_and_releases_all_channels_on_error(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        pi = Mock(connected=True)
        pi.set_servo_pulsewidth.return_value = 0
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=Mock(return_value=pi))}):
            driver = PigpioServos([17,18])
            driver.write(17,1500)
            pi.set_servo_pulsewidth.assert_called_with(17,1500)
            pi.set_servo_pulsewidth.side_effect = [-1,0]
            with self.assertRaises(RuntimeError):
                driver.release([17,18])
            self.assertEqual(pi.set_servo_pulsewidth.call_args.args, (18,0))
            driver.close()
            pi.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
