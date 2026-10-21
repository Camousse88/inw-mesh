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

Use **SquatchMesh-1.2.14-cpu-test4-Pager-merged.bin**, not the application-only
file, for a full install. The merged image includes the 16 MB bootloader,
partition table, blank OTA-selection data (selecting app0), and the application.
It also covers NVS with blank bytes: back up settings and identity first.
The flasher recognises the `-merged.bin` suffix, writes it at 0x0 and automatically
selects a full erase with a warning. This wipes on-device identity, contacts,
settings and messages; export/SD backup is required if they need to be kept.
An application-only image at 0x10000 does not switch a device booting app1 to
app0, and cannot boot an erased device. Never erase when flashing an app-only file.
Check Settings > System > version for `1.2.14-cpu-test4` after installing.

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

## Test4: manual CPU light sleep (experimental)

Settings > Battery > **CPU light sleep** is a separate opt-in, persisted in
`inw-cpu/light` (also absent from exported UI backups). Enable **CPU power
saving** as well. With the screen off, awake work stays at 80 MHz; screen wake
restores the boot clock (240 MHz on this Pager build).

After a 2-minute boot grace period, idle loop intervals can use
`esp_light_sleep_start()` for at most 30 ms. The LoRa receiver is never put to
sleep or RX duty-cycled. Its DIO1 IRQ wakes the CPU; a pending high IRQ prevents
entry. The side button and wheel also wake it; a wheel edge wakes the logical
screen because PCNT does not count while asleep. Keyboard FIFO and motion are
polled on the short timer. The radio GPIO's rising-edge IRQ is restored after
each attempt. No RAM/flash power-down is forced. There is a FreeRTOS yield
between attempts; a sleep error disables further attempts until the toggle is
changed. The elapsed counter includes the measured sleep-call overhead, not a
precise measurement of time with CPU clocks stopped.

Sleep is inhibited while the screen is on, USB/VBUS is present, sound plays,
Bluetooth is enabled (including advertising), Wi-Fi is enabled (including
joining/scanning), the GPS rail is powered, the radio is not ready/in receive
mode, or MeshCore reports pending work. Other synchronous tasks finish before
sleep is attempted; scheduled work still runs on periodic loop wakeups.

For the initial test, turn Bluetooth and Wi-Fi OFF in Settings and unplug USB.
GPS may be off or between the existing background GPS windows. When Bluetooth
is OFF, the disabled service's Bluedroid host and controller are also suspended
before sleeping: merely stopping advertising is insufficient. Re-enabling
Bluetooth resumes the existing controller/host before the service; bonds are
not erased. Neither light-sleep nor CPU-saving toggles automatically change
user radio/GPS settings. This version does not maintain BLE/Wi-Fi connections
through light sleep; that requires a different automatic-sleep/IDF build.

Battery shows the sleep count and elapsed milliseconds since boot. Let the
screen sleep on battery for a minute after the boot grace period, then open
Battery and verify the counters increased. While inspecting the menu the state
will correctly say `screen on`. The USB `cpu` command reports the same counters
and last wake cause; USB inhibits new sleeps but does not clear prior counters.

Required hardware checks, not established by host tests or compilation:
- Repeated radio/button/wheel/keyboard/raise-to-wake cycles, without resets.
- Messages with screen wake disabled: receive/ACK/retry and complete sound at
  80 MHz, then subsequent sleep cycles; bursts at the actual configured SF/BW.
- Re-enable Bluetooth after accumulated sleep cycles; reconnect and sync the
  phone. Repeat enabling Wi-Fi, GPS and plugging/unplugging USB. Confirm sleep
  counters stop for each blocking feature and resume after it stops.
- SD writes, automatic adverts and scheduled alarms continue correctly.
- Compare to test3 with identical settings and traffic on battery. No measured
  autonomy gain or on-device stability claim is made for this build yet.
