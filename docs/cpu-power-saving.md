# Experimental CPU power saving (T-LoRa Pager)

Settings > Battery > CPU power saving is off by default and persists across
reboots in the `inw-cpu/saving` NVS key. It is independent of Battery saver:
enabling it does not change GPS, Bluetooth, Wi-Fi, radio settings or loop delays.
The existing UI settings blob is unchanged, including its backup format.
The new CPU preference is not part of exported UI settings backups.

When the logical screen state is off, the CPU switches to 80 MHz immediately,
including during notification banners, audio playback, mesh RX/TX, GPS updates
and background storage work. The clock captured at boot is restored at logical
screen wake, before the panel/wake animation, or when the setting is disabled. Clock changes
use Arduino's `setCpuFrequencyMhz`; failed requests are reported over USB serial.
The CPU-clock option alone does not sleep the CPU. LoRa stays in continuous RX.
A separate experimental light-sleep option is described below.

The approach is informed by Wadamesh beta_98's screen-dependent 240/80 MHz
switching, with an opt-in setting and the pager's existing 30 ms idle delay.

## Build

```sh
python tools/test_cpu_power.py
python tools/test_cpu_sleep.py
python tools/test_motion_sleep.py
pio run -e t-lora-pager
```

The full build includes NFC. `t-lora-pager-public` omits NFC. For an already
installed Squatch Mesh, the application image is
`.pio/build/t-lora-pager/firmware.bin`.
The fork's "CPU power saving test build" GitHub Actions workflow also uploads
this image, the bootloader, partition table and source commit as a build artifact.
It does not publish a site or an OTA release.
Export a backup before flashing. Do not erase flash for this test. Use an
installer that supports the existing partition layout and active OTA slot;
a launcher or an OTA update may have selected a different application slot.
This fork does not change the
official OTA URL or signing keys, so an official update can replace the test build.

## On-device checks (required before an upstream PR)

### MeshCore web flasher / Custom Firmware

Use **SquatchMesh-1.2.14-cpu-test7-Pager-merged.bin**, not the application-only
file, for a full install. The merged image includes the 16 MB bootloader,
partition table, blank OTA-selection data (selecting app0), and the application.
It also covers NVS with blank bytes: back up settings and identity first.
The flasher recognises the `-merged.bin` suffix, writes it at 0x0 and automatically
selects a full erase with a warning. This wipes on-device identity, contacts,
settings and messages; export/SD backup is required if they need to be kept.
An application-only image at 0x10000 does not switch a device booting app1 to
app0, and cannot boot an erased device. Never erase when flashing an app-only file.
Check Settings > System > version for `1.2.14-cpu-test7` after installing.

### Hardware validation

1. With the setting off, check normal operation and the normal CPU clock.
2. Enable it, turn the screen off and wait at least two seconds. The serial log
   should show `[power] CPU 80 MHz`. Wake with the side button; it should show
   the original clock before the wake animation. Repeat with raise-to-wake and
   message-triggered wake, then with message-triggered wake disabled.
   The USB command `cpu` reports the preference and actual clock without waking
   the screen, so it can confirm the clock while the panel is dark.
3. Send and receive direct/channel messages with the screen off. Check delivery
   acknowledgements, retries and queued messages. Repeat with a phone connected
   over BLE and Wi-Fi enabled, including a contact sync.
4. Play notifications with screen wake disabled: the CPU must remain at 80 MHz
   during the banner and sound, not just after they finish. Check for audio gaps.
   Send/receive messages and exercise background storage while it stays dark. Check the map, SD backup, USB console and update checks.
5. Disable the setting and reboot; it must remain off. Enable and reboot; it
   must remain on and downclock only after the screen goes dark.
6. Compare average battery current with the setting off/on under identical
   screen, GPS, BLE/Wi-Fi and mesh-traffic conditions. Use battery-side current
   measurements or a controlled long discharge; USB input current while charging
   is not a measurement of battery discharge. Check that no resets or extra
   message losses appear. No power or reliability claims are made before this.

Test3: CPU status/log lines use CRLF for compatibility with the MeshCore web
console. The `cpu` command no longer needs a following `themes` command.

## Test7: CPU sleep until an event or scheduled work (experimental)

Settings > Battery > **CPU light sleep** is a separate opt-in, persisted in
`inw-cpu/light` (also absent from exported UI backups). Enable **CPU power
saving** as well. Awake work stays at 80 MHz with the logical screen off;
screen wake restores the boot clock (240 MHz on this build).

The 30 ms sleep slices of test4 are removed, and test5's 5-second battery limit is removed. After the 2-minute boot grace,
`esp_light_sleep_start()` sleeps until the LoRa DIO1 IRQ, the middle/side button
(GPIO0), or the earliest useful deadline. Sleep is capped at 30 seconds, as in
MeshCore/EasySkyMesh ESP32 repeaters. With both CPU options ON and the logical screen OFF, the Pager's battery
reading, charging/saver checks and RTC battery checkpoint now share one
30-second schedule. Normal screen-on operation or either CPU option OFF uses
5-second readings. Critical-low-voltage confirmation uses elapsed time (one
minute), not twelve readings, so changing the cadence does not make it six
minutes. There is no independent 5-second battery/checkpoint wake left. A radio IRQ or button can wake it immediately in that interval.
Deadlines use signed millis differences and are rechecked after preparation.
Pending DM retries/login, adverts, GPS window starts, external sensor readings
and alert-output edges can require an earlier timer wake. USB insertion can be
noticed on the next scheduled battery check (up to about 30 s while sleeping).
The LoRa receiver remains in continuous RX. No RX duty cycling is added.

### Original input behavior preserved

Verified against SquatchMesh 1.2.14 (`8ef76e1`, `src/main.cpp`): once the logical
screen is OFF, keyboard and wheel input is discarded. There is no additional
long-inactivity threshold. The middle/side button wakes the screen; raise to
wake can also do so if enabled. Notification wake follows the existing option.
Test4's added wheel wake has been removed. Keyboard, wheel rotation and wheel
press have no sleep wake source and do not inhibit sleep if held. On subsequent
radio/timer/button wake, the original loop drains and ignores their buffered
input while dark. CPU timer/radio wakes do not themselves light the screen.

Test7 replaces the unconditional motion-polling block with autonomous event
watching when the logical screen is OFF and both CPU options are ON. The source
check is SensorLib 0.4.1's `BHI260AP_TiltDetector` example: it uses the same
`BOSCH_APP30_SHUTTLE_BHI260_FW` image as this build, configures virtual sensor 48
(Tilt Detector) at 1 Hz (event enable, not periodic samples), and supports IRQ
operation. The BHI260AP datasheet lists 48 and 55 (Significant Motion) as wake-up
events; LilyGo wires HIRQ to GPIO8. This is a source-level feasibility check,
not a successful physical sensor test.

At runtime the code checks `bhy2_is_sensor_available` for events 48 and 55 on the
actual running image. Tilt is required for raise/quiet; significant motion is
also armed if available. After at least 3 s of active sampling and 2.5 s without
measured movement, raw accelerometer ODR is set to zero and read back. Only
wake-event/fault interrupts remain enabled, active-high and level-triggered.
GPIO8 becomes a light-sleep wake source only in this state. No 25 Hz raw samples
are enabled during event watch. Tilt and significant motion are thresholded
algorithms: not every tiny vibration is guaranteed to trigger either event.

An event only wakes the CPU at 80 MHz while the screen remains logically OFF.
It drains the FIFO, disables the event sensors, restores the original interrupt
mask, and starts a short 25 Hz sampling window. The existing orientation/raise
heuristic receives the last attitude as a seed and alone decides screen wake;
`raise to wake` OFF still prevents motion from lighting it. With continuous
movement, the CPU can remain awake at 80 MHz to finish sampling. Once stable it
rearms the events; one-shot significant motion is explicitly rearmed. A button,
message-screen wake, option change or setting change resumes the normal path.
Callbacks are removed on exit to avoid accumulating registrations each cycle.

`quiet when face down` can stay ON. Before a notification decides sound/light,
an event-watch sensor resumes raw samples for a bounded fresh orientation check
(3 samples, at most 180 ms). Cached face-down silence is kept only if the fresh
attitude still agrees and no motion IRQ was pending. A motion IRQ clears the old
face-down state and starts the original 2-second stillness qualification again.
The sample burst does not light the screen itself and stays at 80 MHz if dark.

**Man-down remains on the original continuous-polling path in test7.** Its small
movement/immobility thresholds are not equivalent to Tilt/Significant Motion,
so no event-only safety-alarm behavior is claimed. If man-down is enabled, the
CPU does not use long sleep and Battery says `man-down uses polling`.
Unsupported tilt, configuration/readback failure, unexpected/stuck IRQ cause a
visible fallback to normal sampling. This deliberately inhibits long sleep
rather than claiming the sensor is watching when it is not. No motion-related
user setting is automatically disabled.

### Guards and observations

Sleep is also inhibited by the screen/USB, audio playback, enabled BLE/Wi-Fi,
GPS rail power, pending mesh work, unfinished backlight/panel shutdown,
notifications/effects, pending settings saves and active field tools.
The background store must be idle; its queue mutex is held across sleep so
another core cannot begin flash work during entry. Synchronous work completes
before the attempt. GPIO wake configuration is removed and the radio's rising
edge ISR configuration is restored after each attempt. No flash/PSRAM power-down
is forced. A FreeRTOS yield follows wake. On sleep failure, attempts stop until
the option is toggled.

As in test4, with Bluetooth explicitly OFF the inactive Bluedroid host and
controller are suspended before sleeping and resumed before Bluetooth is
re-enabled. Bonds are kept. BLE/Wi-Fi connections are not maintained through
manual light sleep. Radio/GPS/motion preferences are not changed automatically.

Battery also shows motion-watch state and event count. USB `cpu` prints event
capabilities (tilt/significant), GPIO level and state. Battery and USB show
sleep count, accumulated elapsed milliseconds, last and
longest measured sleep-call duration and wake cause. The measurement includes
entry/exit overhead. USB inhibits new sleeps but keeps prior counters. In Battery
the current state will say `screen on`; inspect last/max durations to confirm
long sleeps. Hardware validation is required before an upstream PR:

1. With CPU options OFF, compare input behavior to official firmware.
2. With both options ON, BLE/Wi-Fi OFF and USB unplugged, after the 2-minute
   grace period leave it dark and stationary for a minute. Motion options may
   stay ON, except man-down. Keyboard/wheel must not light it.
   The middle button must wake it immediately. Last/max should be seconds.
3. With message-screen wake OFF, verify messages/ACKs/retries and sound at 80 MHz,
   followed by new sleeps. With message-screen wake ON, verify screen wake.
4. Verify automatic adverts, battery reading/checkpoint, queued saves, external
   sensor/alert output and GPS window boundaries continue on schedule.
5. With raise-to-wake ON, tilt from a flat position into reading position: IRQ
   events should increase, raw samples should resume, and the original raise
   decision should light the screen. With raise OFF and quiet-face-down ON, the
   same event must leave the screen OFF. Verify face-down notifications stay
   silent and turning face-up permits sound; test messages arriving during a
   turn, during the burst, and after >30 s without movement. Repeat many cycles.
6. Enable man-down: long sleep must stop and the original alarm behavior remain.
   Missing events/configuration/readback/stuck-IRQ are simulated in host tests;
   hardware fallback must be checked if observed. Re-enable BLE/Wi-Fi and sync;
   test USB insertion.
7. Compare battery current/autonomy to test3 under matching settings and traffic.
   Compilation/host tests establish neither on-device stability nor power gain.

With no motion features in use, the existing apply() stops sampling and no IMU
IRQ wake is added. With only stay-on-held enabled, the raw sensor is paused while
the screen is off and resumed on screen wake; it needs no motion wake source.
With raise/quiet in use, Battery should show an IRQ watch while sleeping, and a
sampling state after an event. All measurements remain necessary on the Pager.
