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
  constexpr unsigned reasonCount = (unsigned)SleepReason::Count;
  const char* const reasonNames[] = {
    "work: input/USB", "work: GPS", "work: Wi-Fi", "work: battery/sensors/mesh",
    "work: UI tick", "work: drawing", "work: effects", "work: storage/field", "work: scheduling",
    "sleep", "off", "CPU saver off", "sleep error; toggle to retry", "screen on",
    "boot grace (2 min)", "USB power", "sound playing", "Bluetooth on", "Wi-Fi on",
    "GPS active", "motion sensor polling", "NFC active", "background work",
    "panel not asleep", "backlight fading", "notification/overlay", "keyboard effect",
    "preferences pending", "UI settings pending", "field tool/GPS", "storage queue/busy",
    "scheduled work due", "radio not ready", "mesh busy", "radio IRQ pending",
    "middle button held", "80 MHz unavailable", "mesh: invalid pool baseline",
    "mesh: outbound queue", "mesh: allocated RX/TX packet", "radio: not in receive mode"
  };
  static_assert(sizeof(reasonNames)/sizeof(*reasonNames) == reasonCount, "diagnostic labels");
  uint64_t s_darkUs[reasonCount] = {}, s_litUs = 0;
  uint32_t s_hits[reasonCount] = {}, s_timerWakes = 0, s_gpioWakes = 0, s_otherWakes = 0;
  int64_t s_accountAt = 0, s_windowAt = 0;
  bool s_tracking = false, s_dark = false;
  SleepReason s_reason = SleepReason::Scheduling, s_background = SleepReason::Background, s_mesh = SleepReason::Mesh;
  void account() {
    const int64_t now = esp_timer_get_time();
    if (s_tracking && now >= s_accountAt) {
      const uint64_t elapsed = (uint64_t)(now - s_accountAt);
      if (s_dark) s_darkUs[(unsigned)s_reason] += elapsed;
      else s_litUs += elapsed;
    }
    if (!s_tracking) { s_tracking = true; s_windowAt = now; }
    s_accountAt = now;
  }
  void phase(SleepReason reason, bool dark) {
    account(); s_reason = reason; s_dark = dark;
  }
  bool blocked(SleepReason reason, bool dark) {
    s_status = reasonNames[(unsigned)reason];
    ++s_hits[(unsigned)reason];
    phase(reason, dark);
    return false;
  }

}
void sleepDiagnosticPhase(SleepReason reason, bool screenOff) { phase(reason, screenOff); }
void sleepDiagnosticContext(SleepReason background) { s_background = background; }
void sleepDiagnosticMeshContext(SleepReason mesh) { s_mesh = mesh; }
uint64_t sleepDiagnosticMicros(SleepReason reason) { account(); return s_darkUs[(unsigned)reason]; }
void resetSleepDiagnostics(bool screenOff) {
  for (unsigned i=0; i<reasonCount; ++i) { s_darkUs[i]=0; s_hits[i]=0; }
  s_litUs=0; s_timerWakes=s_gpioWakes=s_otherWakes=0;
  s_count=s_wake=s_lastMs=s_maxMs=0; s_micros=0;
  s_tracking=true; s_windowAt=s_accountAt=esp_timer_get_time();
  s_reason=SleepReason::Input; s_dark=screenOff;
  // Do not clear a sleep failure or change preferences/wake sources.
}
void printSleepDiagnostics() {
  account();
  uint64_t dark=0;
  for (unsigned i=0; i<reasonCount; ++i) dark+=s_darkUs[i];
  const uint64_t sleep=s_darkUs[(unsigned)SleepReason::Sleeping];
  Serial.printf("[diag] window_ms=%llu screen_off_ms=%llu screen_on_ms=%llu sleep_pct_dark=%.1f\r\n",
    (unsigned long long)((s_accountAt-s_windowAt)/1000), (unsigned long long)(dark/1000),
    (unsigned long long)(s_litUs/1000), dark ? 100.0*(double)sleep/(double)dark : 0.0);
  for (unsigned i=0; i<reasonCount; ++i) {
    if (!s_darkUs[i] && !s_hits[i]) continue;
    Serial.printf("[diag] %s: dark_ms=%llu dark_pct=%.1f checks=%lu\r\n", reasonNames[i],
      (unsigned long long)(s_darkUs[i]/1000), dark ? 100.0*(double)s_darkUs[i]/(double)dark : 0.0,
      (unsigned long)s_hits[i]);
  }
  Serial.printf("[diag] wakes timer=%lu gpio=%lu other=%lu; first blocking reason only\r\n",
    (unsigned long)s_timerWakes, (unsigned long)s_gpioWakes, (unsigned long)s_otherWakes);
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
  using R = SleepReason;
  const R reason = !lightSleep() ? R::Off : !cpuSaving() ? R::CpuOff :
    s_failed ? R::Error : !in.screenOff ? R::ScreenOn :
    millis() < 120000 ? R::Boot : in.usb ? R::Usb :
    in.audio ? R::Audio : in.bluetooth ? R::Bluetooth :
    in.wifi ? R::Wifi : in.gps ? R::Gps :
    in.motion ? R::Motion : in.nfc ? R::Nfc : in.background ? s_background :
    budgetMs < 2 ? R::Deadline : !in.radioReady ? R::RadioNotReady : in.meshBusy ? s_mesh : R::Count;
  if (reason != R::Count) return blocked(reason, in.screenOff);
  if (digitalRead(PIN_LORA_DIO1) == HIGH) return blocked(R::RadioIrq, in.screenOff);
  if (digitalRead(PIN_BUTTON) == LOW) return blocked(R::Button, in.screenOff);
  updateCpu(true);
  if (cpuClockMhz() != 80) return blocked(R::Clock, in.screenOff);

  // Like MeshCore's ESP32 repeater: mask this core's ISR servicing while the
  // radio GPIO has a level wake trigger, then restore its rising-edge IRQ.
  // Sleep until an IRQ or the earliest useful deadline, up to 30 seconds.
  // Keyboard and wheel deliberately have NO wake trigger, matching SquatchMesh.
  phase(R::Sleeping, in.screenOff);
  esp_err_t err = esp_sleep_enable_timer_wakeup((uint64_t)budgetMs * 1000ULL);
  portENTER_CRITICAL(&s_mux);
  if (err == ESP_OK) err = gpio_wakeup_enable((gpio_num_t)PIN_LORA_DIO1, GPIO_INTR_HIGH_LEVEL);
  if (err == ESP_OK) err = gpio_wakeup_enable((gpio_num_t)PIN_BUTTON, GPIO_INTR_LOW_LEVEL);
  if (err == ESP_OK) err = esp_sleep_enable_gpio_wakeup();
  const int64_t start = esp_timer_get_time();
  if (err == ESP_OK) err = esp_light_sleep_start();
  const int64_t elapsed = esp_timer_get_time() - start;
  const uint32_t wake = (uint32_t)esp_sleep_get_wakeup_cause();
  gpio_wakeup_disable((gpio_num_t)PIN_LORA_DIO1);
  gpio_set_intr_type((gpio_num_t)PIN_LORA_DIO1, GPIO_INTR_POSEDGE);
  gpio_wakeup_disable((gpio_num_t)PIN_BUTTON);
  gpio_set_intr_type((gpio_num_t)PIN_BUTTON, GPIO_INTR_DISABLE);
  portEXIT_CRITICAL(&s_mux);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_GPIO);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_TIMER);
  if (err != ESP_OK) {
    s_failed = true;
    s_reason = R::Error; // Failed calls are not counted as sleep time.
    blocked(R::Error, in.screenOff);
    Serial.printf("[power] light sleep failed: %d\r\n", (int)err);
    return false;
  }
  phase(R::Scheduling, in.screenOff);
  if (wake == ESP_SLEEP_WAKEUP_TIMER) ++s_timerWakes;
  else if (wake == ESP_SLEEP_WAKEUP_GPIO) ++s_gpioWakes;
  else ++s_otherWakes;
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
