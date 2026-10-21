#pragma once
#include <Arduino.h>

namespace power {
  bool lightSleep();
  bool setLightSleep(bool on);
  // All fields are evaluated on the main loop after synchronous background work.
  struct SleepInputs {
    bool screenOff, audio, usb, bluetooth, wifi, gps, meshBusy, radioReady;
  };
  // Returns true if a light-sleep attempt consumed this loop's idle interval.
  bool idleLightSleep(const SleepInputs& in);
  bool takeSleepWheelWake(); // wheel edge/press observed during a sleep
  const char* lightSleepStatus();
  uint32_t lightSleepCount();
  uint64_t lightSleepMicros();
  uint32_t lightSleepWakeCause();
}
