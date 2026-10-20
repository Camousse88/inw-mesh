# Experimental CPU power saving (T-LoRa Pager)

Settings > Battery > CPU power saving is off by default and persists across
reboots in the `inw-cpu/saving` NVS key. It is independent of Battery saver:
enabling it does not change GPS, Bluetooth, Wi-Fi, radio settings or loop delays.
The existing UI settings blob is unchanged, including its backup format.
The new CPU preference is not part of exported UI settings backups.

Once the screen, panel and backlight are off, the CPU switches to 80 MHz.
The clock captured at boot is restored before waking the panel, during audio
playback or an active UI overlay, and when the setting is disabled. Clock changes
use Arduino's `setCpuFrequencyMhz`; failed requests are reported over USB serial.
There is no ESP light sleep or deep sleep in this feature. LoRa stays in RX.

The approach is informed by Wadamesh beta_98's screen-dependent 240/80 MHz
switching, with an opt-in setting and the pager's existing 30 ms idle delay.

## Build

```sh
python tools/test_cpu_power.py
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

Use **SquatchMesh-1.2.14-cpu-test2-Pager-merged.bin**, not the application-only
file, for a full install. The merged image includes the 16 MB bootloader,
partition table, blank OTA-selection data (selecting app0), and the application.
It also covers NVS with blank bytes: back up settings and identity first.
The flasher recognises the `-merged.bin` suffix, writes it at 0x0 and automatically
selects a full erase with a warning. This wipes on-device identity, contacts,
settings and messages; export/SD backup is required if they need to be kept.
An application-only image at 0x10000 does not switch a device booting app1 to
app0, and cannot boot an erased device. Never erase when flashing an app-only file.
Check Settings > System > version for `1.2.14-cpu-test2` after installing.

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
4. Play a notification with the screen off, check the sound and the return to
   80 MHz when done. Check the map, SD backup, USB console and update checks.
5. Disable the setting and reboot; it must remain off. Enable and reboot; it
   must remain on and downclock only after the screen goes dark.
6. Compare average battery current with the setting off/on under identical
   screen, GPS, BLE/Wi-Fi and mesh-traffic conditions. Use battery-side current
   measurements or a controlled long discharge; USB input current while charging
   is not a measurement of battery discharge. Check that no resets or extra
   message losses appear. No power or reliability claims are made before this.
