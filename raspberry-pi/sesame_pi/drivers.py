"""Hardware imports are lazy so simulation works without Raspberry Pi packages."""
import logging

log = logging.getLogger(__name__)


class SimulatedServos:
    def __init__(self):
        self.pulses = {}

    def write(self, gpio, pulse):
        self.pulses[gpio] = pulse
        log.debug("GPIO %s -> %s us", gpio, pulse)

    def release(self, pins):
        for gpio in pins:
            self.write(gpio, 0)

    def close(self):
        pass


class PigpioServos:
    def __init__(self, pins):
        import pigpio

        self.pins = tuple(pins)
        self.pi = pigpio.pi("127.0.0.1", 8888)
        if not self.pi.connected:
            self.pi.stop()
            raise RuntimeError("Cannot reach local pigpiod. Start it with: sudo pigpiod -l")
        try:
            self.release(self.pins)
        except Exception:
            self.pi.stop()
            raise

    def write(self, gpio, pulse):
        # Servo API emits one pulse every 20 ms (50 Hz). Do not replace this
        # with an arbitrary PWM duty cycle or a higher digital-servo frequency.
        result = self.pi.set_servo_pulsewidth(gpio, pulse)
        if result < 0:
            raise RuntimeError(f"pigpio rejected GPIO {gpio}: {result}")

    def release(self, pins):
        # Try every output even if a preceding channel fails.
        errors = []
        for gpio in pins:
            try:
                self.write(gpio, 0)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def close(self):
        self.pi.stop()
