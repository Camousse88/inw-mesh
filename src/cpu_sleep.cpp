#include "cpu_sleep.h"
#include "power.h"
#include "sleep_deadline.h"
#include "board_pins.h"
#include <Preferences.h>
#include <esp_sleep.h>
#include <esp_timer.h>
#include <driver/gpio.h>

namespace power {
namespace {
  int8_t s_enabled = -1;
  bool s_failed = false;
  uint32_t s_count = 0, s_wake = 0, s_lastMs = 0, s_maxMs = 0;
  uint64_t s_micros = 0;
  const char* s_status = "not checked";
  portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
}
bool lightSleep() {
  if (s_enabled < 0) {
    s_enabled = 0;
    Preferences p;
    if (p.begin("inw-cpu", true)) {
      s_enabled = p.getBool("light", false) ? 1 : 0;
      p.end();
    }
  }
  return s_enabled == 1;
}
bool setLightSleep(bool on) {
  Preferences p;
  if (!p.begin("inw-cpu", false)) return false;
  const bool saved = p.putBool("light", on) == sizeof(bool);
  p.end();
  if (!saved) return false;
  s_enabled = on ? 1 : 0;
  s_failed = false;
  return true;
}
const char* lightSleepStatus() { return s_status; }
uint32_t lightSleepCount() { return s_count; }
uint32_t lightSleepLastMs() { return s_lastMs; }
uint32_t lightSleepMaxMs() { return s_maxMs; }
uint64_t lightSleepMicros() { return s_micros; }
uint32_t lightSleepWakeCause() { return s_wake; }

bool idleLightSleep(const SleepInputs& in) {
  // Manual light sleep is deliberately separate from the working 80 MHz policy.
  // Never suspend I2S, GPS UART, a wireless stack or an outbound mesh operation.
  SleepDeadline timer(millis());
  timer.at(in.deadline);
  const uint32_t budgetMs = timer.ms();
  const char* block = !lightSleep() ? "off" : !cpuSaving() ? "CPU saver off" :
    s_failed ? "sleep error; toggle to retry" : !in.screenOff ? "screen on" :
    millis() < 120000 ? "boot grace (2 min)" : in.usb ? "USB power" :
    in.audio ? "sound playing" : in.bluetooth ? "Bluetooth on" :
    in.wifi ? "Wi-Fi on" : in.gps ? "GPS active" :
    in.motion ? "motion sensor polling" : in.background ? "background work" :
    budgetMs < 2 ? "scheduled work due" : !in.radioReady ? "radio not ready" : in.meshBusy ? "mesh busy" : nullptr;
  if (block) { s_status = block; return false; }
  // Wake sources are level-sensitive, so a packet racing the final check will
  // wake immediately too. Do not enter with an already-pending IRQ/button.
  if (digitalRead(PIN_LORA_DIO1) == HIGH || digitalRead(PIN_BUTTON) == LOW) {
    s_status = "pending IRQ/button";
    return false;
  }
  if (in.imuIrq && digitalRead(PIN_IMU_INT) == HIGH) {
    s_status = "pending motion IRQ";
    return false;
  }
  updateCpu(true);
  if (cpuClockMhz() != 80) { s_status = "80 MHz unavailable"; return false; }

  // Like MeshCore's ESP32 repeater: mask this core's ISR servicing while the
  // radio GPIO has a level wake trigger, then restore its rising-edge IRQ.
  // Sleep until an IRQ or the earliest useful deadline, up to 30 seconds.
  // Keyboard and wheel deliberately have NO wake trigger, matching SquatchMesh.
  esp_err_t err = esp_sleep_enable_timer_wakeup((uint64_t)budgetMs * 1000ULL);
  portENTER_CRITICAL(&s_mux);
  if (err == ESP_OK) err = gpio_wakeup_enable((gpio_num_t)PIN_LORA_DIO1, GPIO_INTR_HIGH_LEVEL);
  if (err == ESP_OK) err = gpio_wakeup_enable((gpio_num_t)PIN_BUTTON, GPIO_INTR_LOW_LEVEL);
  if (err == ESP_OK && in.imuIrq) err = gpio_wakeup_enable((gpio_num_t)PIN_IMU_INT, GPIO_INTR_HIGH_LEVEL);
  if (err == ESP_OK) err = esp_sleep_enable_gpio_wakeup();
  const int64_t start = esp_timer_get_time();
  if (err == ESP_OK) err = esp_light_sleep_start();
  const int64_t elapsed = esp_timer_get_time() - start;
  const uint32_t wake = (uint32_t)esp_sleep_get_wakeup_cause();
  gpio_wakeup_disable((gpio_num_t)PIN_LORA_DIO1);
  gpio_set_intr_type((gpio_num_t)PIN_LORA_DIO1, GPIO_INTR_POSEDGE);
  gpio_wakeup_disable((gpio_num_t)PIN_BUTTON);
  gpio_set_intr_type((gpio_num_t)PIN_BUTTON, GPIO_INTR_DISABLE);
  if (in.imuIrq) {
    gpio_wakeup_disable((gpio_num_t)PIN_IMU_INT);
    gpio_set_intr_type((gpio_num_t)PIN_IMU_INT, GPIO_INTR_DISABLE);
  }
  portEXIT_CRITICAL(&s_mux);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_GPIO);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_TIMER);
  if (err != ESP_OK) {
    s_failed = true;
    s_status = "sleep error; toggle to retry";
    Serial.printf("[power] light sleep failed: %d\r\n", (int)err);
    return false;
  }
  ++s_count;
  if (elapsed > 0) s_micros += (uint64_t)elapsed;
  s_wake = wake;
  s_lastMs = elapsed > 0 ? (uint32_t)(elapsed / 1000) : 0;
  if (s_lastMs > s_maxMs) s_maxMs = s_lastMs;
  s_status = "ready (event/deadline wake)";
  // Always yield so IDLE/tasks/watchdogs run between manual sleeps.
  delay(1);
  return true;
}
} // namespace power
