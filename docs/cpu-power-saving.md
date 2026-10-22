# Experimental CPU power saving (T-LoRa Pager)

Settings > Battery > battery saver > **CPU : power saving** is the single
user-facing control for CPU downclocking and light sleep. Its short description
is "sleep CPU, keep messages". It turns both existing preferences on/off together;
`inw-cpu/saving` and `inw-cpu/light` are retained for compatibility, including an
already enabled test9 installation. It is off by default. It is independent of Battery saver:
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

Use **SquatchMesh-1.2.14-cpu-test10-Pager-merged.bin**, not the application-only
file, for a full install. The merged image includes the 16 MB bootloader,
partition table, blank OTA-selection data (selecting app0), and the application.
It also covers NVS with blank bytes: back up settings and identity first.
The flasher recognises the `-merged.bin` suffix, writes it at 0x0 and automatically
selects a full erase with a warning. This wipes on-device identity, contacts,
settings and messages; export/SD backup is required if they need to be kept.
An application-only image at 0x10000 does not switch a device booting app1 to
app0, and cannot boot an erased device. Never erase when flashing an app-only file.
Check Settings > System > version for `1.2.14-cpu-test10` after installing.

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

## Test9: CPU sleep until an event or scheduled work (experimental)

Test10 replaces the two experimental toggles and diagnostic rows with the
single **CPU : power saving** toggle, alongside the existing Battery saver.
The description is directly below it. USB `cpu`/`motion` diagnostics remain
available; System already displays the firmware version. Both preferences are
absent from exported UI backups. An incomplete old pair is shown OFF; enabling
it synchronizes both to ON. Disabling turns both OFF. The second-write failure
path attempts to restore the first preference and reports the failure.

This is a menu change; the test9 sleep, radio, motion and alert algorithms remain
the same. Awake work stays at 80 MHz with the logical screen off;
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
wake can also do so if enabled outside screen-off saving. Notification wake follows the existing option.
Test4's added wheel wake has been removed. Keyboard, wheel rotation and wheel
press have no sleep wake source and do not inhibit sleep if held. On subsequent
radio/timer/button wake, the original loop drains and ignores their buffered
input while dark. CPU timer/radio wakes do not themselves light the screen.

Test8 introduced the following screen-off policy, retained in test9. It replaces test7's motion IRQ watch with a strict screen-off pause.
With both CPU options ON and the logical screen OFF, raw accelerometer sampling
is stopped immediately, without waiting for immobility. The configured ODR must
read back as zero; failure falls back to polling and visibly blocks long sleep.
Neither Tilt nor Significant Motion is armed. GPIO8 is never a CPU wake source,
and a high IMU line does not prevent sleep. Walking cannot extend an active
sampling window because there is no such window.

Raise-to-wake is suspended while screen-off saving is active, including during
an alert read. The saved motion preferences are retained. The middle button
still wakes the screen, and notification screen wake still follows the original
setting. Screen wake or disabling either CPU option restores normal sampling.
Stay-on-held only samples while the screen is on in this mode.

`quiet when face down` remains available without motion wakes. Immediately
before deciding notification sound/light, the paused accelerometer takes three
consecutive fresh, valid and stable 25 Hz samples, then stops immediately.
A cold restart is allowed up to 500 ms, including settling; normal reads stop as
soon as the three samples arrive. Invalid startup samples reset the streak,
not the whole alert result. Silence requires a stable orientation screen-down
within 30 degrees, acceleration magnitude between 0.85 and 1.15 g, and successive
vector changes no greater than 0.08 g. A stable face-up reading completes just as
quickly and permits sound. A failed/timeout read permits sound rather than using
stale orientation. The sensor is retried on the next message and successful
resume clears a previous transient error. The read never raises the screen.

Test9 fixes two weaknesses in test8's alert read: a single invalid startup sample
latched rejection for the whole read, and its 180 ms limit could expire before
three samples arrived after a cold restart. A successful subsequent resume also
failed to clear the sensor failure flag. The regression simulation reproduces a
quiet first message followed by failed reads when later restarts take 240 ms and
start with zero samples. Twelve successive face-down alerts now stay quiet;
turning face-up permits sound, and the next message recovers from a transient
resume error without reboot or setting changes. This simulates a mechanism
consistent with the observed symptom; hardware traces are still needed to
confirm the precise restart behavior on the user's sensor.

The short local stability check replaces the normal path's two-second
qualification while asleep; it cannot prove the Pager was stationary before
the message arrived. No cached orientation is trusted. USB `cpu`/`motion` now
includes the last alert result and read duration for hardware diagnosis.

**Man-down remains on the original continuous-polling path.** Enabling this
alarm blocks long sleep, as Battery's `man-down uses polling` status explains.
The alarm is not silently disabled or given different immobility semantics.
Raise-to-wake remains suspended while the screen-off saving policy is active.

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

Battery shows the motion state and explains that raise-to-wake is paused in
light-sleep mode. USB `cpu` reports `motion_wake=off`. Battery and USB retain
sleep count, accumulated elapsed milliseconds, last/max sleep-call duration and
wake cause. These durations include entry/exit overhead. USB inhibits new sleeps
but retains counters. Hardware checks required before an upstream PR:

1. With both CPU options ON, BLE/Wi-Fi OFF, USB unplugged and man-down OFF,
   wait past the two-minute boot grace. Walk with the Pager in a pocket: the
   screen must remain dark, and motion must not cause continuous CPU activity.
2. Verify the middle button wakes immediately. Keyboard, wheel rotation and
   wheel press must not wake it; saved raise-to-wake must resume outside this mode.
3. With message-screen wake OFF, verify messages/ACKs/retries and sound at 80 MHz,
   followed by sleep. With message-screen wake ON, verify the original screen rule.
4. With quiet-face-down ON, send messages face-down, face-up, while turning and
   while walking. Turn it over during a long sleep before sending another message
   to check that old orientation cannot keep sound muted. Repeat many cycles.
5. Verify battery checks at 30 s while dark, scheduled adverts/retries, storage,
   external sensors, GPS windows, USB insertion and BLE/Wi-Fi re-enabling.
6. Enable man-down: long sleep must stop; original alarm behavior must remain.
7. Compare battery-side current/autonomy with test7 under matching conditions.
   Compilation and simulated tests establish neither hardware stability nor gain.
