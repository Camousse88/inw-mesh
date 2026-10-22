#pragma once

namespace app {
#if defined(INW_NFC) && INW_NFC
  bool nfcActive(); // read, write or tag-emulation session not yet closed
#else
  inline bool nfcActive() { return false; }
#endif
}
