# Sesame on Raspberry Pi 4

This port targets **Raspberry Pi 4, Raspberry Pi OS Trixie, and eight MG90S
position servos**, without a physical face display. Eight GPIO pins send servo
control pulses directly using `pigpio`; no PCA9685 is required.
The original ESP32 firmware and mechanical files remain available.

Implemented: all 15 poses, forward/backward/left/right gaits, original face
artwork as browser previews, browser controls, JSON command/status API,
per-servo calibration, interruptible motion, and a continuous-movement watchdog.
Simulation runs on macOS, Linux, or Windows without GPIO hardware.

**Status:** software port with automated simulation tests. Actual pulse timing,
servo travel, walking stability, and power behavior still need
verification on your Pi and assembled robot. Start with one unloaded servo.

## Try simulation

From this directory, with Python 3.9 or later:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m sesame_pi serve
```

Open [the controller](http://127.0.0.1:8765). Simulation is the default and never
imports the Pi hardware drivers. It shows the face and commanded servo targets;
it does not simulate physics or certify that the robot can balance.

## Wiring

Numbers in configuration are **BCM GPIO numbers**, not header positions or the
ESP32 pin numbers. The servo order matches `movement-sequences.h` exactly:

| Servo | Firmware index | BCM GPIO | Physical Pi header pin |
| --- | --- | --- | --- |
| R1 | 0 | 17 | 11 |
| R2 | 1 | 18 | 12 |
| L1 | 2 | 27 | 13 |
| L2 | 3 | 22 | 15 |
| R4 | 4 | 23 | 16 |
| R3 | 5 | 24 | 18 |
| L3 | 6 | 25 | 22 |
| L4 | 7 | 16 | 36 |

Connect each servo's signal lead to its assigned GPIO. Connect servo power leads
to an **external regulated supply matching the servos' voltage specification**
(typically 5 V for this build). Size the supply and wiring for simultaneous
movement and stall current; the original 5 V/3 A recommendation is not a guarantee
for every servo or mechanical load. Do not power eight servos from the Pi header.
Connect servo supply ground to Pi ground (for example physical pin 6); route
motor return current directly to the external supply. Power the Pi through its
normal USB-C supply for initial testing. Do not connect the external supply's
positive lead to the Pi's power pins.

Pi GPIO signals are 3.3 V. Check that your servo accepts this control level; use
a suitable signal buffer if it requires a higher logic level. Never feed 5 V
back into a Pi GPIO. See the [Pi GPIO documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)
and [Adafruit's servo wiring guide](https://learn.adafruit.com/using-servos-with-circuitpython/hardware).

No OLED, I2C setup, Adafruit display libraries, or screen wiring is needed.
Face events remain in the motion assets for API compatibility and browser
previews; they perform no GPIO or I2C operations.

## MG90S control profile

The port uses pulse-width position control, not a DC motor voltage output or a
continuous-rotation speed command. The starting profile is:

| Setting | Starting value |
| --- | --- |
| Repetition rate | 50 Hz (one pulse every 20 ms) |
| Logical 0 | 1000 µs pulse |
| Logical 90 / neutral | 1500 µs pulse |
| Logical 180 | 2000 µs pulse |

The driver uses `pigpio.set_servo_pulsewidth`, whose servo output repeats at
50 Hz. It never copies the ESP32's 732–2929 µs pulse range, and it never treats
these pulses as a generic percentage duty cycle. Each servo has independent
minimum, neutral, and maximum pulse settings, so an asymmetric measured range
still preserves the neutral position. Pulse mapping is linear on each side of
neutral, with direction, trim, and mechanical limits applied first.

**These are cautious starting commands, not a guarantee of 180° travel for your
particular motors.** MG90S-labelled units differ. The
[TowerPro product page](https://towerpro.com.tw/product/mg90s-3/) does not specify
pulse endpoints or a minimum accepted signal-high voltage. One
[MG90S datasheet](https://www.makerhero.com/img/files/download/Servo-mg90s-Datasheet.pdf)
describes 1–2 ms pulses and a 20 ms period, whereas another manufacturer's
[MG90S datasheet](https://www.tinytronics.nl/product_files/000263_Data%20Sheet%20of%20MG90S%20Analog%20Servo%20Motor.pdf)
specifies 500–2500 µs for 180° travel. Match the supplier's datasheet and measure
your own units before changing endpoints. If your units are continuous-rotation
MG90S variants, these position gaits do not apply.

Pi output is 3.3 V; a pulse-width setting does not raise its logic voltage.
Verify that the actual servo recognizes it, or use an appropriate non-inverting
logic buffer. Never connect a 5 V output back into the Pi. Removing the PCA9685
does not remove the need for an external servo power supply and common ground.

## Raspberry Pi installation

Target: **Raspberry Pi 4**, Raspberry Pi OS Trixie (normally Python 3.13). The
application supports Python 3.9+. This `pigpio` backend
does not target Pi 5. The Python client and the native `pigpiod` daemon are
different components: installing Python dependencies alone does not install
the daemon. Package availability depends on the OS release.

```bash
sudo apt update
sudo apt install -y git python3-venv build-essential
git clone --branch raspberry-pi-port --single-branch https://github.com/Crazy-Bull/sesame-robot.git
cd sesame-robot
cd raspberry-pi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[hardware]'
cp config.example.json config.json
```

The Raspberry Pi port is on the `raspberry-pi-port` branch. Run these commands
on the Pi; its Python environment is created there rather than copied from
another computer. `cp config.example.json config.json` is for the first setup
only: keep your own calibrated `config.json` when updating.

Check for the native daemon with `command -v pigpiod`. Trixie's
[Debian pigpio packages](https://packages.debian.org/source/trixie/pigpio) include
the Python client and tools but not the native daemon. Do not assume installing
`python3-pigpio` gives you `pigpiod`. For a Pi 4 without an existing daemon,
build the tagged [upstream v79 release](https://github.com/joan2937/pigpio/releases/tag/v79)
on the Pi:

```bash
git clone --depth 1 --branch v79 https://github.com/joan2937/pigpio.git /tmp/sesame-pigpio-v79
make -C /tmp/sesame-pigpio-v79 -j2 pigpiod pigs
sudo install -m 755 /tmp/sesame-pigpio-v79/libpigpio.so.1 /usr/local/lib/
sudo install -m 755 /tmp/sesame-pigpio-v79/pigpiod /tmp/sesame-pigpio-v79/pigs /usr/local/bin/
sudo ldconfig
```

This installs only the native library, daemon, and command tool. It avoids the
upstream `make install` target's legacy system-Python installation; the Python
client is already installed in this project's virtual environment. These Pi
build instructions have not been executed on your hardware yet. They are based
on the [tagged upstream Makefile](https://github.com/joan2937/pigpio/blob/v79/Makefile).
Do not substitute a Pi 5 GPIO library while keeping this backend unchanged.
Start exactly one daemon, restricted to local connections:

```bash
sudo /usr/local/bin/pigpiod -l -f -n 127.0.0.1
```

The `-n 127.0.0.1` option is required with this controller: pigpio v79 can bind
`-l` to IPv6 loopback (`::1`) only, while our client explicitly uses IPv4
(`127.0.0.1`). The address allowlist forces the upstream daemon to use its IPv4
socket path while keeping it local-only. See the
[upstream socket initialization](https://github.com/joan2937/pigpio/blob/v79/pigpio.c).

If a distribution service already runs it, configure that service with the same
options instead of starting a second instance. The application runs as your
normal user; it connects to `127.0.0.1:8888`.

If Python reports a connection failure, inspect the running daemon and listener:

```bash
pgrep -a -x pigpiod
sudo ss -ltnp 'sport = :8888'
```

A listener at `[::1]:8888` does not serve our IPv4 client. With the robot
supported and servo power off, stop the controller and restart its daemon with
the IPv4 option above. `Can't lock /var/run/pigpio.pid` usually means an existing
instance is still running; do not remove its PID file to bypass the lock. If you
installed a daemon service, stop/restart that service instead of running a
second manual instance.

## Update or develop directly on the Pi

Support the robot before stopping the controller: shutdown releases its servo
pulses. Stop a foreground server with Ctrl+C. If you installed the example boot
service, stop it with `sudo systemctl stop sesame-pi` instead. Update the Pi's
checkout from GitHub:

```bash
cd ~/sesame-robot
git status --short
git pull --ff-only
cd raspberry-pi
source .venv/bin/activate
python -m pip install -e '.[hardware]'
python -m unittest discover -s tests -v
python -m sesame_pi --config config.json --hardware serve --host 0.0.0.0
```

If using the boot service, restart it with `sudo systemctl start sesame-pi`
instead of the final foreground command. Hardware starts disarmed again;
inspect the changes before arming.

The virtual environment and your calibrated `raspberry-pi/config.json` are
ignored by Git and are preserved by pulls. Do not copy the example over your
calibration during updates. If `git status` shows tracked source edits, commit
them before pulling; `--ff-only` refuses to create an unexpected merge.

You can also edit the Python source directly on the Pi. The editable install
uses those source files after restarting the app. Run the simulation tests,
then commit your selected source files and push this branch back to GitHub.
Read-only cloning and pulling this public repository do not require a GitHub
login; pushing changes requires your GitHub account's Git credentials on the Pi.

## Calibrate before enabling whole-robot movements

Keep the robot supported, remove servo horns/linkages for the first tests, and
have a physical servo-power switch within reach. Run only one controller or
calibration command at a time: `pigpiod` does not give this program exclusive
ownership of pins.

`config.json` is local and ignored by Git. For each named servo it contains:

- `gpio`: BCM signal pin.
- `min_pulse_us` / `max_pulse_us`: pulses corresponding to physical 0° / 180°.
  Defaults are the conservative **1000–2000 µs**, not the ESP32's 732–2929 µs.
  The default span may produce less than 180° of actual travel on your servos.
- `neutral_pulse_us`: measured neutral pulse, initially **1500 µs**. It must
  lie strictly between the minimum and maximum; it maps to physical command 90°.
- `reverse`: invert logical direction; `offset`: add physical trim in degrees.
- `min_angle` / `max_angle`: clamp the transformed physical target to a tested
  range. Clamping is reported as `limited` in status and in the browser.
- `calibrated`: set to `true` only after verifying that channel's pulse span,
  direction, neutral alignment, and safe travel.

First move one servo to its neutral command, then test small changes:

```bash
python -m sesame_pi --config config.json --hardware calibrate R1 90
python -m sesame_pi --config config.json --hardware calibrate R1 85
python -m sesame_pi --config config.json --hardware calibrate R1 95
```

Each command controls only the selected servo and releases its pulses after
two seconds (adjustable with `--seconds`, at most five). Repeat for all channels.
Confirm names against the original leg layout. Extend travel gradually only
within the servo manufacturer's limits; the backend accepts 500–2500 µs, so
it deliberately cannot reproduce the original firmware's 2929 µs endpoint.
Mount horns at verified neutral alignment and recheck the mechanism's limits.
Configuration maps logical command angles to pulses; these are not position
measurements and must not be confused with verified mechanical angles.

After all eight channels are calibrated:

```bash
python -m sesame_pi --config config.json --hardware serve --host 0.0.0.0
```

Open `http://<pi-address>:8765`, press
**Arm**, then test Rest, Stand, Wave, and short direction holds while supporting
the robot. Hardware always starts disarmed and does not move on startup. Arming
requires all eight `calibrated` flags; it does not itself move any servo.

## Stop behavior and API

- **Stop** cancels movement immediately between individual servo writes and
  holds the last commanded targets. It does not move to Stand automatically.
- **Emergency release / Disarm** cancels movement and disables pulses on all
  eight outputs. The robot may collapse; disabling pulses is not a power cut.
  An explicit Arm is required before more motion.
- Direction commands continue while heartbeats arrive. The default timeout is
  0.8 seconds; timeout cancels the gait and holds targets. Browser controls send
  heartbeats every 150 ms while held and stop on release or loss of focus.
- One-shot poses finish without heartbeats, but can be interrupted by any new
  movement, Stop, Disarm, or Emergency release.
- Normal shutdown and SIGTERM disable pulses. An application crash, SIGKILL,
  frozen operating system, or failed daemon connection can leave PWM active:
  the Python watchdog is not a hardware emergency stop. Use a physical power
  disconnect for servo power. A driver fault disarms the app and attempts to
  disable every channel; resolve the fault and restart before rearming.

The original JSON endpoint names are retained:

```bash
curl http://127.0.0.1:8765/api/status
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' -d '{"command":"arm"}'
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' -d '{"command":"wave","face":"happy"}'
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' -d '{"face":"thinking"}'
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' -d '{"command":"forward"}'
# Send repeatedly while moving (e.g. every 150 ms):
curl -X POST http://127.0.0.1:8765/api/heartbeat \
  -H 'Content-Type: application/json' -d '{}'
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' -d '{"command":"stop"}'
```

Commands: `rest`, `stand`, `wave`, `dance`, `swim`, `point`, `pushup`, `bow`,
`cute`, `freaky`, `worm`, `shake`, `shrug`, `dead`, `crab`, `forward`, `backward`,
`left`, `right`, plus `arm`, `stop`, `estop`, `disarm`. Repeating the current
direction renews its timeout without restarting the gait. An explicit `face`
with a movement overrides that movement's face events. Face-only requests do
not renew movement heartbeats.

`GET /api/settings` and `POST /api/settings` expose `frameDelay` (10–2000 ms)
and `motorCurrentDelay` (0–200 ms). Settings changes are in-memory; edit the
configuration for persistence. `GET /api/face` returns a PNG preview.

The app uses Waitress with one robot instance and multiple HTTP threads.
Use one process: multiple WSGI workers or multiple app instances would fight
for the same GPIO. The default bind address is local-only. LAN mode has no
authentication and is for a trusted local network; do not forward its port to
the internet. Cross-origin browser POST requests are rejected.

The ESP32 captive portal, runtime Wi-Fi provisioning, serial CLI, legacy GET
control endpoints, Bluetooth gamepad support, and random idle blinks are not
ported yet. Configure Wi-Fi through Raspberry Pi OS. The new browser controller
uses the JSON API. Missing upstream `stand`/`defualt` face bitmaps fall back to
the existing idle image; all other exported bitmap bytes are unchanged.

## Development and verification

```bash
python -m unittest discover -s tests -v
python tools/export_assets.py --check
```

The exporter compiles the **actual** `movement-sequences.h` with a small host
Arduino shim and records servo, face, and wait calls. It records two gait cycles
and verifies their repetition before saving one loop; gait entry is stored
separately so it is not repeated on every cycle. It extracts original 128×64
MSB-first bitmap rows and frame rates. The JSON assets include source hashes and
ship inside the installed package; no C++ compiler is needed on the robot.
After upstream firmware changes, run `python tools/export_assets.py` and review
the resulting assets. A host C++ compiler is required only for this export.

Hardware acceptance still required:

- Verify all eight GPIO signals, pulse ranges, neutral positions and directions.
- Verify a 20 ms pulse period, a 1500 µs neutral signal, and each channel's
  calibrated endpoints with an oscilloscope or logic analyzer if available.
- Test Rest/Stand/Wave and each gait with short holds under mechanical support.
- Check release, stop, disconnect timeout, and shutdown before free walking.
- Measure supply voltage under load and check for resets, binding or overheating.

Optional boot startup can use `deploy/sesame-pigpiod.service` and
`deploy/sesame-pi.service.example` after these checks. Edit the app service's
username and checkout paths before installing. The daemon unit assumes the
`/usr/local/bin/pigpiod` installation above; avoid running it alongside a second
distribution-provided daemon service. The app still starts disarmed after every
reboot or restart.
