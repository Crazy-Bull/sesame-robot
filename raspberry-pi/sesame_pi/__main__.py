"""Command line entry point. Simulation is the default; hardware is explicit."""
import argparse
import logging
import signal
import sys
import time

from .config import Config, SERVO_NAMES, number
from .drivers import PigpioServos, SimulatedServos
from .faces import Faces
from .robot import Robot


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="Sesame Robot for Raspberry Pi 4")
    parser.add_argument("--config", help="Path to servo calibration JSON")
    parser.add_argument("--hardware", action="store_true", help="Use local pigpiod instead of simulation")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="action", required=True)
    serve = sub.add_parser("serve", help="Start browser controls and JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    calibrate = sub.add_parser("calibrate", help="Move only one servo, then release it")
    calibrate.add_argument("servo", choices=SERVO_NAMES)
    calibrate.add_argument("angle", type=float, help="Logical angle, 0 to 180")
    calibrate.add_argument("--seconds", type=float, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    driver = None
    robot = None
    server = None
    try:
        config = Config.load(args.config)
        if args.action == "calibrate":
            servo = config.servos[SERVO_NAMES.index(args.servo)]
            angle, pulse, limited = servo.target(args.angle)
            number(args.seconds, "seconds", 0.1, 5)
            driver = PigpioServos([servo.gpio]) if args.hardware else SimulatedServos()
            print(f"{'HARDWARE' if args.hardware else 'SIMULATION'}: {args.servo}, "
                  f"BCM GPIO {servo.gpio}, physical angle {angle:g}, {pulse} us"
                  + (" (limited by calibration)" if limited else ""), flush=True)
            try:
                driver.write(servo.gpio, pulse)
                time.sleep(args.seconds)
            finally:
                driver.release([servo.gpio])
            return
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        driver = PigpioServos([s.gpio for s in config.servos]) if args.hardware else SimulatedServos()
        robot = Robot(config, driver, Faces(), simulation=not args.hardware)
        from waitress import create_server
        from .web import create_app

        server = create_server(create_app(robot), host=args.host, port=args.port, threads=4)
        logging.info("%s at http://%s:%s; outputs %s",
                     "Hardware" if args.hardware else "Simulation", args.host, args.port,
                     "disarmed" if args.hardware else "simulated")
        server.run()
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, ImportError, OSError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1
    finally:
        if robot is not None:
            robot.close()
        else:
            if driver is not None:
                driver.close()
        if server is not None:
            server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
