#!/usr/bin/env python3
"""Real battery tick and extracted node/NFC methods against simulated hardware.

These checks cover the three test11 integration guards, not physical NFC/radio
reliability. Reuse the IDF fakes from test_cpu_sleep without executing that test.
"""
from pathlib import Path
import ast
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
module = ast.parse((ROOT / 'tools/test_cpu_sleep.py').read_text())
FILES = next(ast.literal_eval(node.value) for node in module.body
             if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'FILES'
                     for target in node.targets))
FILES['Arduino.h'] += r'''
#include <cmath>
#include <algorithm>
#include <cstring>
using std::min; using std::max; using std::abs;
template<class T> T constrain(T value,T lo,T hi) { return min(hi,max(lo,value)); }
'''
FILES['Wire.h'] = r'''#pragma once
#include <Arduino.h>
class TwoWire { public:
 unsigned requests=0;
 void beginTransmission(uint8_t) {}
 size_t write(uint8_t) { return 1; }
 size_t write(const uint8_t*,size_t count) { return count; }
 int endTransmission(bool=true) { return 0; }
 int requestFrom(uint8_t,uint8_t) { ++requests; return 0; }
 int read() { return 0; }
};
inline TwoWire Wire;
'''

def function(path, signature):
    """Keep the production body intact; unrelated UI/mesh dependencies are faked."""
    source = (ROOT / path).read_text()
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

node_ctor = function('src/node.cpp', 'InwNode::InwNode(')
node_work = function('src/node.cpp', 'bool InwNode::radioWorkPending() const')
nfc_active = function('src/nfcapp.cpp', 'bool app::nfcActive()')
FILES['test.cpp'] = r'''#include <Arduino.h>
#include <Wire.h>
#include <cassert>
#include <cstdio>
#include <Preferences.h>
#include <esp_sleep.h>
#include <driver/gpio.h>
#include "cpu_sleep.h"
#include "battery_schedule.h"
#include "nfcapp.h"
#define private public
#include "battery.h"
#undef private
namespace power {
 bool cpuSaving() { return true; }
 void updateCpu(bool) {}
 uint32_t cpuClockMhz() { return 80; }
}
namespace mesh { struct Radio {}; struct RNG {}; struct RTCClock {}; }
struct SimpleMeshTables {}; struct DataStore {};
struct Pool { int freeCount=16; int getFreeCount() const { return freeCount; } } pool;
class MyMesh { public:
 Pool* _mgr=&pool;
 bool outboundOrDirty=false;
 MyMesh(mesh::Radio&,mesh::RNG&,mesh::RTCClock&,SimpleMeshTables&,DataStore&,void*) {}
 bool hasPendingWork() const { return outboundOrDirty; }
};
class InwNode : public MyMesh { public:
 int _idlePacketCount=0;
 DataStore& _ds;
 int _echo[1],discovered[1],pktLog[1],cliLog[1];
 InwNode(mesh::Radio&,mesh::RNG&,mesh::RTCClock&,SimpleMeshTables&,DataStore&);
 bool radioWorkPending() const;
};
enum class Mode { Off,Read,Write,Emulate };
Mode s_mode=Mode::Off;
''' + node_ctor + '\n' + node_work + '\n' + nfc_active + r'''
Battery batteryAt(uint32_t start,bool drift=false) {
 Battery b;
 b._w=&Wire; b._gauge=true; b._haveReading=true;
 b._last=b._lastDump=start; b._resynced=true;
 b._mahKnown=true; b._shown=true;
 b._mah=drift?1000:0;
 b._percent=20;
 b._millivolts=drift?3600:3390;
 b._restMv=drift?3800:3390;
 b._currentMa=drift?0:-200;
 return b;
}
uint32_t criticalAt(uint32_t interval) {
 auto b=batteryAt(1000);
 LowBatteryWindow low;
 for(uint32_t time=1000+interval;time<1000000;time+=interval) {
  now=time;
  assert(b.tick(time,interval));
  if(low.update(b.percent()<=1 && b.millivolts()<3400,time)) return time-1000;
 }
 assert(false); return 0;
}
int main() {
 // Six 5-second model updates equal one 30-second reading. Only the arithmetic
 // catches up: the 30-second tick must still make a single group of I2C reads.
 for(bool drift : {false,true}) {
  auto fast=batteryAt(1000,drift),slow=batteryAt(1000,drift);
  for(uint32_t time=6000;time<=31000;time+=5000) { now=time; assert(fast.tick(time)); }
  Wire.requests=0; now=31000;
  assert(slow.tick(now,30000) && Wire.requests==6);
  assert(slow.percent()==fast.percent() && slow._restMv==fast._restMv);
  assert(std::abs(slow.remainingMah()-fast.remainingMah())<0.001f);
 }
 auto b=batteryAt(1000); now=31000; assert(b.tick(now,30000)); assert(b.percent()==14);
 assert(!b.tick(32000,30000)); // no hidden extra gauge polling
 // Keep fractional elapsed time across irregular readings / screen transitions.
 auto irregular=batteryAt(1000);
 for(uint32_t time=6500;time<=56000;time+=5500) { now=time; assert(irregular.tick(now,5500)); }
 assert(irregular.percent()==9); // 55 s = eleven model ticks, not ten readings
 // Critical shutdown confirmation keeps the same 60 s window; sampling may
 // delay seeing the threshold by <= one 30 s reading, never by sixfold smoothing.
 const auto fastCritical=criticalAt(5000),slowCritical=criticalAt(30000);
 assert(fastCritical==155000 && slowCritical==180000);
 auto wrap=batteryAt(0xfffffff0u); now=(uint32_t)(0xfffffff0u+30000u);
 assert(wrap.tick(now,30000) && wrap.percent()==14);
 // Charging keeps its original monotonic upward-only display behavior.
 auto charging=batteryAt(1000); charging._currentMa=100; charging._mah=1200;
 now=31000; assert(charging.tick(now,30000) && charging.percent()==26);
 // The real node constructor captures the actual free-pool baseline. One or
 // several inbound packets must block sleep until every packet is released.
 mesh::Radio radio; mesh::RNG rng; mesh::RTCClock rtc;
 SimpleMeshTables tables; DataStore store;
 InwNode node(radio,rng,rtc,tables,store);
 assert(node._idlePacketCount==16 && !node.radioWorkPending());
 now=120000; assert(power::setLightSleep(true));
 power::SleepInputs idle{}; idle.screenOff=idle.radioReady=true; idle.deadline=now+30000;
 for(int freeCount : {15,14,15}) {
  pool.freeCount=freeCount;
  assert(!node.hasPendingWork() && node.radioWorkPending());
  idle.meshBusy=node.radioWorkPending();
  assert(!power::idleLightSleep(idle) && calls==0);
 }
 pool.freeCount=16; idle.meshBusy=node.radioWorkPending();
 assert(!idle.meshBusy && power::idleLightSleep(idle) && calls==1);
 node.outboundOrDirty=true; assert(node.radioWorkPending()); node.outboundOrDirty=false;
 node._idlePacketCount=0; assert(node.radioWorkPending()); node._idlePacketCount=16;
 // Test every real NFC mode against the actual light-sleep gate, then close it.
 for(Mode mode : {Mode::Read,Mode::Write,Mode::Emulate}) {
  s_mode=mode; idle.nfc=app::nfcActive();
  assert(idle.nfc && !power::idleLightSleep(idle) && calls==1);
  assert(std::strcmp(power::lightSleepStatus(),"NFC active")==0);
 }
 s_mode=Mode::Off; idle.nfc=app::nfcActive();
 assert(!idle.nfc && power::idleLightSleep(idle) && calls==2);
 std::printf("Battery model/time/critical window, allocated radio packets and NFC sleep guards: passed\n");
}
'''
with tempfile.TemporaryDirectory(prefix='sleep-integration-') as directory:
    temp = Path(directory)
    for name, content in FILES.items():
        path = temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    binary = temp / 'check'
    subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-DINW_NFC=1',
                    f'-I{temp}', f'-I{ROOT / "src"}', str(ROOT / 'src/cpu_sleep.cpp'),
                    str(temp / 'test.cpp'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    # The public/no-NFC build must not require linking nfcapp.cpp.
    (temp / 'no_nfc.cpp').write_text('#include "nfcapp.h"\nint main() { return app::nfcActive(); }\n')
    subprocess.run(['g++', '-std=c++17', f'-I{ROOT / "src"}', str(temp / 'no_nfc.cpp'),
                    '-o', str(temp / 'no-nfc')], check=True)
    subprocess.run([str(temp / 'no-nfc')], check=True)
    print('No-NFC stub: passed')
