#pragma once
#include <Arduino.h>

namespace power {
  bool lightSleep();
  bool setLightSleep(bool on);
  // All fields are evaluated on the main loop after synchronous background work.
  struct SleepInputs {
    bool screenOff, audio, usb, bluetooth, wifi, gps, meshBusy, radioReady;
    bool motion, background, nfc;
    bool imuIrq; // reserved compatibility field; motion never wakes the CPU
    uint32_t deadline; // absolute millis() of earliest application deadline
  };
  // Returns true if a light-sleep attempt consumed this loop's idle interval.
  bool idleLightSleep(const SleepInputs& in);
  const char* lightSleepStatus();
  uint32_t lightSleepCount();
  uint32_t lightSleepLastMs();
  uint32_t lightSleepMaxMs();
  uint64_t lightSleepMicros();
  uint32_t lightSleepWakeCause();
}
