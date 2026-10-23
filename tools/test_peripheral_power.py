#!/usr/bin/env python3
"""Real peripheral drivers and extracted app transitions against bus/RFAL fakes."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def function(path, signature):
    text = (ROOT / path).read_text()
    start = text.index(signature)
    brace = text.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

FILES = {
'Arduino.h': r'''#pragma once
#include <cstdint>
#include <cstddef>
#include <cassert>
#include <vector>
#include <array>
inline uint32_t now=1000;
inline uint32_t millis() { return now; }
inline void delay(uint32_t ms) { now+=ms; }
inline void delayMicroseconds(uint32_t) {}
constexpr int LOW=0,HIGH=1,INPUT=0,OUTPUT=1,OPEN_DRAIN=2;
inline int modes[50]={},levels[50]={};
inline void pinMode(int pin,int mode) { modes[pin]=mode; }
inline void digitalWrite(int pin,int value) { levels[pin]=value; }
''',
'Wire.h': r'''#pragma once
#include <Arduino.h>
struct Write { uint8_t addr,reg,val; };
class TwoWire { public:
 uint8_t regs[128][256]={},addr=0,selected=0;
 std::vector<uint8_t> tx;
 std::vector<Write> writes;
 int failOnceReg=-1; bool failReads=false,failWrites=false;
 void beginTransmission(uint8_t a) { addr=a; tx.clear(); }
 size_t write(uint8_t v) { tx.push_back(v); return 1; }
 int endTransmission(bool=true) {
   if(tx.empty()) return 0;
   selected=tx[0];
   if(tx.size()==2) {
     if(failWrites || selected==failOnceReg) { failOnceReg=-1; return 1; }
     regs[addr][selected]=tx[1]; writes.push_back({addr,selected,tx[1]});
   }
   return 0;
 }
 int requestFrom(uint8_t a,uint8_t n) { addr=a; return failReads ? 0 : n; }
 int read() { return regs[addr][selected]; }
};
inline TwoWire Wire;
''',
'driver/i2s.h': r'''#pragma once
#include <Arduino.h>
using i2s_port_t=int; using i2s_mode_t=int;
constexpr int I2S_NUM_0=0,I2S_MODE_MASTER=1,I2S_MODE_TX=2,I2S_BITS_PER_SAMPLE_16BIT=16;
constexpr int I2S_CHANNEL_FMT_ONLY_LEFT=0,I2S_COMM_FORMAT_STAND_I2S=0,I2S_PIN_NO_CHANGE=-1,ESP_OK=0;
struct i2s_config_t { int mode,sample_rate,bits_per_sample,channel_format,communication_format,dma_buf_count,dma_buf_len; bool tx_desc_auto_clear; };
struct i2s_pin_config_t { int mck_io_num,bck_io_num,ws_io_num,data_out_num,data_in_num; };
inline bool installed=false,installFail=false,pinsFail=false;
inline int installs=0,uninstalls=0;
inline int i2s_driver_install(int,const i2s_config_t*,int,void*) {
 assert(!installed); if(installFail) return 1; installed=true; ++installs; return 0;
}
inline int i2s_set_pin(int,const i2s_pin_config_t*) { return pinsFail?1:0; }
inline void i2s_zero_dma_buffer(int) { assert(installed); }
inline int i2s_driver_uninstall(int) { assert(installed); installed=false; ++uninstalls; return 0; }
inline void i2s_write(int,const void*,size_t,size_t*,int) {}
#define pdMS_TO_TICKS(x) (x)
''',
}
source = r'''#include <Arduino.h>
#include <Wire.h>
#include "haptic.h"
#include "es8311_codec.h"
#include "nfcapp.h"
struct Expander {
 bool rails[16]={};
 void enableRail(int pin,int ms) { rails[pin]=true; delay(ms); }
 void digitalWrite(int pin,int value) {
  if(pin==EXP_NFC_EN && value==LOW) assert(modes[PIN_NFC_CS]==OPEN_DRAIN && levels[PIN_NFC_CS]==HIGH);
  rails[pin]=value;
 }
} expander;
struct Gps { unsigned starts=0; void begin() { ++starts; } } gps;
struct Settings { bool gpsOn=true; } ui_settings;
struct Dimmer { bool off=false; bool asleep() const { return off; } } dimmer;
namespace power { bool saving=false; bool saver() { return saving; } }
namespace field { bool need=false; bool wantsGps() { return need; } }
static bool s_gpsRail=true;
''' + function('src/main.cpp','void gpsPower(bool on)') + '\n' + function('src/main.cpp','static void gpsSchedule()') + '\n' + function('src/main.cpp','void inwNfcPower(bool on)') + r'''
struct Spi { void begin(int,int,int,int) {} } inw_spi;
constexpr int ST_ERR_NONE=0;
inline int initializes=0,deinitializes=0,deactivates=0,discovers=0,initResult=0;
inline bool irqAttached=false;
class RfalRfST25R3916Class { public:
 RfalRfST25R3916Class(Spi*,int,int) {}
 void rfalDeinitialize() { assert(expander.rails[EXP_NFC_EN]); ++deinitializes; irqAttached=false; }
 void rfalListenStop() {} void rfalFieldOff() {}
};
class RfalNfcClass { public:
 RfalNfcClass(RfalRfST25R3916Class*) {}
 int rfalNfcInitialize() { assert(expander.rails[EXP_NFC_EN]); ++initializes; irqAttached=true; return initResult; }
 void rfalNfcDeactivate(bool) { ++deactivates; }
 int rfalNfcDiscover(int*) { ++discovers; return 0; }
};
struct NdefClass { NdefClass(RfalNfcClass*) {} };
enum class Mode { Off,Read,Write,Emulate };
Mode s_mode=Mode::Off;
RfalRfST25R3916Class* s_rf=nullptr;
RfalNfcClass* s_nfc=nullptr;
NdefClass* s_ndef=nullptr;
bool s_ready=false,s_failed=false,s_powered=true,s_handled=false,s_primed=false;
uint32_t s_session=0;
int s_disc=0,s_ceNfcid2=0;
void configListen() {} void configPoll() {} void demoCeInit(int) {}
''' + '\n'.join(function('src/nfcapp.cpp',sig) for sig in [
'bool ensureInit()', 'void stop()', 'void stopSession(uint32_t session)', 'bool start(Mode m)',
'bool app::nfcActive()', 'bool app::nfcRailRequested()', 'void app::nfcIdlePower(bool saving)']) + r'''
int main() {
 // Haptic: GO=1 and a failed read must never get cut by standby.
 Haptic h; assert(h.begin());
 h.idlePower(true); assert(h.standby() && Wire.regs[ADDR_DRV2605_HAPT][1]==0x40);
 auto count=Wire.writes.size(); now+=100; h.idlePower(true); assert(Wire.writes.size()==count);
 const uint8_t pattern[]={14,0x80|22,14};
 h.pattern(pattern,3); assert(!h.standby() && Wire.regs[ADDR_DRV2605_HAPT][0x0c]==1);
 now+=100; h.idlePower(true); assert(!h.standby());
 Wire.failReads=true; Wire.regs[ADDR_DRV2605_HAPT][0x0c]=0;
 now+=100; h.idlePower(true); assert(!h.standby());
 Wire.failReads=false; now+=100; h.idlePower(true); assert(h.standby());
 h.buzz(2); assert(!h.standby() && Wire.regs[ADDR_DRV2605_HAPT][0x0c]==1);
 Wire.regs[ADDR_DRV2605_HAPT][0x0c]=0; now+=100; h.idlePower(true);
 h.tick(); assert(!h.standby() && Wire.regs[ADDR_DRV2605_HAPT][0x0c]==1);
 Wire.regs[ADDR_DRV2605_HAPT][0x0c]=0; now+=100; h.idlePower(true);
 h.idlePower(false); assert(!h.standby() && Wire.regs[ADDR_DRV2605_HAPT][1]==0);
 now+=100; h.idlePower(true); h.setMode(2); assert(!h.standby());
 // Codec is powered down even when there is no startup sound. Repeated stop
 // must not touch an unowned I2S port, including failed install/pin setup.
 Es8311 codec; assert(codec.begin() && codec.sleeping() && !installed && uninstalls==0);
 assert(Wire.regs[ADDR_ES8311_CODEC][0x0d]==0xfc && Wire.regs[ADDR_ES8311_CODEC][1]==0);
 codec.stop(); assert(uninstalls==0);
 for(int i=0;i<8;++i) {
  assert(codec.start() && installed && !codec.sleeping());
  codec.stop(); assert(codec.sleeping() && !installed);
 }
 installFail=true; assert(!codec.start() && codec.sleeping() && !installed); installFail=false;
 pinsFail=true; assert(!codec.start() && codec.sleeping() && !installed); pinsFail=false;
 Wire.failOnceReg=0x17; assert(!codec.start() && codec.sleeping() && !installed);
 assert(codec.start()); Wire.failWrites=true; codec.stop();
 assert(!installed && !codec.sleeping()); Wire.failWrites=false; codec.stop(); assert(codec.sleeping());
 assert(codec.start()); codec.stop(); assert(installs==uninstalls);
 // GPS: restoring OFF after the default-ON boot must reconcile immediately.
 expander.rails[EXP_GPS_EN]=true; ui_settings.gpsOn=false; gpsSchedule();
 assert(!s_gpsRail && !expander.rails[EXP_GPS_EN]);
 ui_settings.gpsOn=true; dimmer.off=false; gpsSchedule(); assert(s_gpsRail);
 now=130000; dimmer.off=true; gpsSchedule(); assert(!s_gpsRail);
 now=1800000; gpsSchedule(); assert(s_gpsRail);
 power::saving=true; gpsSchedule(); assert(!s_gpsRail);
 ui_settings.gpsOn=false; field::need=true; gpsSchedule(); assert(s_gpsRail);
 field::need=false; gpsSchedule(); assert(!s_gpsRail);
 // NFC: no session => deinit and rail OFF; every session restores and re-inits.
 expander.rails[EXP_NFC_EN]=true;
 app::nfcIdlePower(true); assert(!s_powered && !expander.rails[EXP_NFC_EN] && initializes==0);
 for(Mode m:{Mode::Read,Mode::Write,Mode::Emulate}) {
  const int oldInit=initializes;
  assert(start(m) && initializes==oldInit+1 && irqAttached && app::nfcActive());
  app::nfcIdlePower(true); assert(s_powered && expander.rails[EXP_NFC_EN]);
  auto old=s_session; assert(start(Mode::Read));
  stopSession(old); assert(app::nfcActive()); // previous page destructor
  stopSession(s_session); assert(!app::nfcActive());
  app::nfcIdlePower(true); assert(!s_powered && !irqAttached && !s_ready);
 }
 // Disabling saving restores the original rail policy, without eager RFAL work.
 auto oldInit=initializes; app::nfcIdlePower(false);
 assert(s_powered && initializes==oldInit && !s_ready);
 assert(start(Mode::Read)); stop(); app::nfcIdlePower(true);
 initResult=1; assert(!start(Mode::Read) && s_failed);
 app::nfcIdlePower(true); assert(!s_powered && !irqAttached);
 initResult=0; assert(start(Mode::Emulate)); stop(); app::nfcIdlePower(true);
}
'''
FILES['test.cpp'] = source
with tempfile.TemporaryDirectory(prefix='peripheral-power-test-') as directory:
    temp = Path(directory)
    for name, content in FILES.items():
        path = temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    binary = temp / 'check'
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-DINW_NFC=1',
                    f'-I{temp}', f'-I{ROOT / "src"}', str(temp/'test.cpp'), '-o', str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
    print('Haptic sequencer/standby, codec lifecycle/errors, restored GPS and NFC power/session recovery: passed')
