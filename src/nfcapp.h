#pragma once

namespace app {
#if defined(INW_NFC) && INW_NFC
  void nfcIdlePower(bool saving);
  bool nfcRailRequested();
  bool nfcActive(); // read, write or tag-emulation session not yet closed
#else
  inline void nfcIdlePower(bool) {}
  inline bool nfcRailRequested() { return true; } // no-NFC build retains bringup rail policy
  inline bool nfcActive() { return false; }
#endif
}
