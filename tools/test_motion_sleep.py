#!/usr/bin/env python3
"""Compile real motion.cpp against a simulated BHI260AP; no hardware claims."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {
'Arduino.h': r'''#pragma once
#include <cstdint>
#include <cstddef>
#include <cmath>
#include <algorithm>
#include <cstring>
using std::min; using std::max;
template<class T> T constrain(T v, T lo, T hi) { return std::max(lo,std::min(hi,v)); }
inline uint32_t fakeNow=1000;
inline int imuLevel=0;
inline uint32_t millis() { return fakeNow; }
inline void delay(uint32_t ms) { fakeNow+=ms; }
constexpr int INPUT=0, HIGH=1, LOW=0;
inline void pinMode(int,int) {}
inline int digitalRead(int) { return imuLevel; }
struct SerialMock { template<class... T> void printf(const char*,T...) {} void println(const char*) {} };
inline SerialMock Serial;
''',
'Wire.h': r'''#pragma once
#include <cstdint>
class TwoWire { public: void beginTransmission(uint8_t) {} int endTransmission() { return 0; }
uint32_t getClock() { return 400000; } void setClock(uint32_t) {} };
inline TwoWire Wire;
''',
'Preferences.h': r'''#pragma once
#include <cstdint>
class Preferences { public: bool begin(const char*,bool) { return true; }
bool getBool(const char*,bool value) { return value; }
uint8_t getUChar(const char*,uint8_t value) { return value; }
void putBool(const char*,bool) {} void putUChar(const char*,uint8_t) {} void end() {} };
''',
'logstore.h': r'''#pragma once
constexpr int LOG_INFO=0,LOG_WARN=1,LOG_ERROR=2;
class LogStore { public: template<class... T> void add(int,const char*,T...) {} };
''',
'bosch/BoschSensorID.hpp': r'''#pragma once
namespace BoschSensorID { constexpr uint8_t ACCEL_CORRECTED=4,ACCEL_PASSTHROUGH=1,TILT_DETECTOR=48,SIGNIFICANT_MOTION=55; }
''',
'BoschFirmware.h': r'''#pragma once
inline const uint8_t bosch_firmware_image[]={0};
inline const size_t bosch_firmware_size=1;
inline const bool bosch_firmware_type=false;
''',
'SensorBHI260AP.hpp': r'''#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <cassert>
constexpr int BHY2_OK=0;
struct bhy2_dev {};
struct bhy2_virt_sensor_conf { float sample_rate; };
struct bhy2_data_xyz { int16_t x,y,z; };
inline bhy2_data_xyz pose{0,0,4096};
inline void bhy2_parse_xyz(const uint8_t* p,bhy2_data_xyz* v) { std::memcpy(v,p,sizeof(*v)); }
inline bool tiltSupported=true,sigSupported=true,rejectEvents=false,rejectStop=false,rejectResume=false,stuckIrq=false;
inline float rates[256]={};
using Callback=void(*)(uint8_t,const uint8_t*,uint32_t,uint64_t*,void*);
inline Callback callbacks[256]={};
inline int pendingEvent=0;
inline uint8_t interruptMask=8;
inline bool bhy2_is_sensor_available(uint8_t id,const bhy2_dev*) {
return id==48?tiltSupported:id==55?sigSupported:true;
}
inline int bhy2_get_virt_sensor_cfg(uint8_t id,bhy2_virt_sensor_conf* c,bhy2_dev*) {
c->sample_rate=rejectStop && id==4 && rates[id]==0 ? 25 : rates[id]; return 0;
}
struct InterruptConfig {
  bool wakeUpFIFOEnabled=true,nonWakeUpFIFOEnabled=true,statusFIFOEnabled=true,debuggingEnabled=false,faultEnabled=true;
  enum class Polarity { ACTIVE_HIGH,ACTIVE_LOW }; enum class TriggerMode { LEVEL,EDGE };
  Polarity polarity=Polarity::ACTIVE_HIGH; TriggerMode triggerMode=TriggerMode::LEVEL;
  uint8_t getRegisterValue() const { return (!wakeUpFIFOEnabled)|(!nonWakeUpFIFOEnabled<<1)|(!statusFIFOEnabled<<2)|(!debuggingEnabled<<3)|(!faultEnabled<<4); }
};
enum class SensorRemap { BOTTOM_LAYER_TOP_LEFT_CORNER };
class SensorBHI260AP {
  bhy2_dev dev; uint32_t lastRaw=0;
public:
  void setPins(int) {} void setFirmware(const uint8_t*,size_t,bool) {} void setBootFromFlash(bool) {}
  bool begin(TwoWire&,uint8_t) { return true; }
  const char* getError() { return "simulated"; }
  void setRemapAxes(SensorRemap) {} bhy2_dev* getDev() { return &dev; }
  bool onResultEvent(uint8_t id,Callback cb) {
    if (!bhy2_is_sensor_available(id,&dev) || callbacks[id]) return false;
    callbacks[id]=cb; return true;
  }
  void removeResultEvent(uint8_t id,Callback) { callbacks[id]=nullptr; }
  bool configure(uint8_t id,float rate,uint32_t) {
    if (rejectResume && id==4 && rate>0) return false;
    if (rejectEvents && (id==48||id==55) && rate>0) return false;
    rates[id]=rate;
    if (id==4||id==1) lastRaw=millis();
    return true;
  }
  uint8_t getInterruptRegisterValue() { return interruptMask; }
  bool setInterruptRegisterValue(uint8_t v) { interruptMask=v; return true; }
  bool configureInterrupt(const InterruptConfig& c) { interruptMask=c.getRegisterValue(); return true; }
  void update() {
    if (pendingEvent && rates[pendingEvent]>0 && callbacks[pendingEvent]) {
      uint8_t id=pendingEvent; pendingEvent=0;
      if (id==55) rates[id]=0; // Bosch one-shot
      callbacks[id](id,nullptr,0,nullptr,nullptr);
    }
    imuLevel=stuckIrq ? 1 : 0;
    if (rates[4]>0 && callbacks[4] && millis()-lastRaw>=40) {
      lastRaw=millis(); callbacks[4](4,reinterpret_cast<const uint8_t*>(&pose),sizeof(pose),nullptr,nullptr);
    }
  }
};
''',
'motion_test.cpp': r'''#include <cassert>
#include <cstring>
#include <string>
#include <SensorBHI260AP.hpp>
#include <logstore.h>
#include "motion.h"
LogStore logs;
namespace power {
  bool light=true,cpu=true;
  bool saver() { return false; }
  bool lightSleep() { return light; }
  bool cpuSaving() { return cpu; }
}
void frames(int count,bool off=true) {
  for (int i=0;i<count;++i) { fakeNow+=40; motion::tick(off); }
}
int main(int argc,char** argv) {
  std::string scenario=argc>1?argv[1]:"normal";
  if (scenario=="missing") tiltSupported=false;
  if (scenario=="config") rejectEvents=true;
  if (scenario=="stop") rejectStop=true;
  if (scenario=="stuck") stuckIrq=true;
  motion::begin();
  assert(rates[4]==25);
  frames(150);
  if (scenario!="normal") {
    assert(!motion::irqWatch() && motion::needsFastPolling() && rates[4]==25);
    assert(std::strstr(motion::sleepState(),"polling"));
    assert(callbacks[48]==nullptr && callbacks[55]==nullptr);
    return 0;
  }
  assert(motion::irqWatch() && !motion::needsFastPolling() && rates[4]==0);
  assert(rates[48]==1 && rates[55]==1 && interruptMask==14);
  fakeNow+=30000; motion::tick(true);
  assert(motion::irqWatch() && motion::running() && rates[4]==0);
  assert(!motion::takeRaise());
  // A tilt IRQ only starts a raw sampling burst. Existing raise logic decides.
  pose={0,-3277,2458}; pendingEvent=48; imuLevel=1;
  motion::tick(true);
  assert(!motion::irqWatch() && motion::needsFastPolling() && motion::eventCount()==1);
  assert(rates[4]==25 && interruptMask==8 && callbacks[48]==nullptr);
  frames(20);
  assert(motion::takeRaise());
  assert(!motion::takeRaise());
  // Quiet-face-down survives 30 s with raw sampling disabled, and is refreshed
  // before audio; a changed orientation must not keep stale silence enabled.
  motion::setRaiseToWake(false); motion::setStayOnHeld(false); motion::setQuietFaceDown(true);
  pose={0,0,-4096}; frames(200);
  assert(motion::irqWatch() && motion::faceDown());
  fakeNow+=30000; motion::tick(true); assert(motion::faceDown());
  uint32_t before=millis(); motion::refreshForAlert();
  assert(millis()-before<=180 && motion::faceDown() && !motion::irqWatch());
  frames(200); assert(motion::irqWatch());
  pose={0,0,4096}; motion::refreshForAlert(); assert(!motion::faceDown());
  frames(200); assert(motion::irqWatch());
  // Repeated entry/exit must remove callbacks, restore masks and rearm events.
  for (int i=0;i<3;++i) {
    pendingEvent=55; imuLevel=1; motion::tick(true);
    assert(!motion::irqWatch() && rates[4]==25);
    frames(150); assert(motion::irqWatch() && rates[55]==1);
  }
  // Turning the screen or CPU policy on/off resumes the original sample path.
  motion::tick(false); assert(!motion::irqWatch() && rates[4]==25);
  frames(150); assert(motion::irqWatch());
  power::light=false; motion::tick(true); assert(!motion::irqWatch() && rates[4]==25);
  power::light=true; frames(150); assert(motion::irqWatch());
  // A failed accelerometer resume must not pretend it can sleep safely.
  rejectResume=true; motion::tick(false);
  assert(!motion::irqWatch() && motion::needsFastPolling());
  assert(std::strstr(motion::sleepState(),"resume failed"));
  rejectResume=false; motion::setQuietFaceDown(true);
  frames(150); assert(motion::irqWatch());
  // Man-down keeps the proven polling behavior instead of changing its timing.
  motion::setManDownMin(10); frames(150);
  assert(!motion::irqWatch() && motion::needsFastPolling() && rates[4]==25);
  motion::setManDownMin(0); motion::setQuietFaceDown(false);
  assert(!motion::irqWatch() && !motion::needsFastPolling() && rates[4]==0);
}
''',
}
with tempfile.TemporaryDirectory(prefix='motion-sleep-test-') as directory:
    temp = Path(directory)
    for name, content in FILES.items():
        path = temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    # Quoted includes search next to motion.cpp first; use an exact copy of the
    # production translation unit so hardware-only local headers can be faked.
    (temp / 'motion.cpp').write_text((ROOT / 'src/motion.cpp').read_text())
    binary = temp / 'check'
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
                    f'-I{temp}',f'-I{ROOT / "src"}',str(temp/'motion.cpp'),
                    str(temp/'motion_test.cpp'),'-o',str(binary)],check=True)
    for scenario in ('normal','missing','config','stop','stuck'):
        subprocess.run([str(binary),scenario],check=True)
        print(f'Motion sleep {scenario}: passed')
