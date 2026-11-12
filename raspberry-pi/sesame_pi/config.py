"""Validated BCM pin assignments and per-servo calibration."""
from dataclasses import dataclass
import json
import math
from pathlib import Path

SERVO_NAMES = ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4")
DEFAULT_PINS = (17, 18, 27, 22, 23, 24, 25, 16)
SERVO_MODEL = "MG90S"
SERVO_FREQUENCY_HZ = 50  # pigpio.set_servo_pulsewidth uses a fixed 20 ms period.


def number(value, label, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{label} must be a finite number between {low} and {high}")
    return value


@dataclass(frozen=True)
class ServoConfig:
    gpio: int
    min_pulse_us: float = 1000
    neutral_pulse_us: float = 1500
    max_pulse_us: float = 2000
    min_angle: float = 0
    max_angle: float = 180
    offset: float = 0
    reverse: bool = False
    calibrated: bool = False

    def __post_init__(self):
        # Leave GPIO 0/1 (HAT EEPROM) and GPIO 2/3 (I2C pull-ups) unused.
        if type(self.gpio) is not int or self.gpio not in range(4, 28):
            raise ValueError("servo gpio must be a BCM number from 4 to 27")
        for field in ("reverse", "calibrated"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(field + " must be true or false")
        number(self.min_pulse_us, "min_pulse_us", 500, 2500)
        number(self.neutral_pulse_us, "neutral_pulse_us", 500, 2500)
        number(self.max_pulse_us, "max_pulse_us", 500, 2500)
        number(self.min_angle, "min_angle", 0, 180)
        number(self.max_angle, "max_angle", 0, 180)
        number(self.offset, "offset", -90, 90)
        if (not self.min_pulse_us < self.neutral_pulse_us < self.max_pulse_us
                or self.min_angle >= self.max_angle):
            raise ValueError("servo pulses must satisfy minimum < neutral < maximum; angle minimum < maximum")

    def target(self, angle):
        number(angle, "angle", 0, 180)
        physical = (180 - angle if self.reverse else angle) + self.offset
        limited = max(self.min_angle, min(self.max_angle, physical))
        # Keep neutral at its measured pulse even when endpoints are asymmetric.
        if limited <= 90:
            pulse = self.min_pulse_us + limited / 90 * (self.neutral_pulse_us - self.min_pulse_us)
        else:
            pulse = self.neutral_pulse_us + (limited - 90) / 90 * (self.max_pulse_us - self.neutral_pulse_us)
        pulse = round(pulse)
        return limited, pulse, limited != physical


@dataclass(frozen=True)
class Config:
    servos: tuple
    frame_delay_ms: float = 100
    motor_delay_ms: float = 20
    command_timeout_s: float = 0.8

    def __post_init__(self):
        if len(self.servos) != 8 or not all(isinstance(s, ServoConfig) for s in self.servos):
            raise ValueError("Exactly eight servo configurations are required")
        if len({s.gpio for s in self.servos}) != 8:
            raise ValueError("Each servo must use a different GPIO")
        number(self.frame_delay_ms, "frame_delay_ms", 10, 2000)
        number(self.motor_delay_ms, "motor_delay_ms", 0, 200)
        number(self.command_timeout_s, "command_timeout_s", 0.2, 5)

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls(tuple(ServoConfig(pin) for pin in DEFAULT_PINS))
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object")
        data = dict(data)
        servo_data = data.pop("servos", None)
        if not isinstance(servo_data, dict) or set(servo_data) != set(SERVO_NAMES):
            raise ValueError("servos must contain exactly " + ", ".join(SERVO_NAMES))
        try:
            servos = tuple(ServoConfig(**servo_data[name]) for name in SERVO_NAMES)
            return cls(servos, **data)
        except TypeError as exc:
            raise ValueError("Invalid configuration fields: " + str(exc)) from exc
