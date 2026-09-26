# Whale URL-based Deck preset switching

This optional extension reports only the focused tab's scheme, hostname, and path to the locally running QuickSlot Deck. Query strings, fragments, and browsing history are not sent or stored.

1. In QuickSlot Deck, open **환경 설정 → 프리셋 자동 전환**. Add site rules such as `youtube.com`, `naver.com`, or `tradingview.com/chart`, select their Deck presets, and enable automatic switching. Ordinary application rules such as `obs64.exe` work without this browser extension.
2. In Whale, open extension management, enable developer mode, and load this `browser_extension` directory as an unpacked extension.
3. Open this extension's options and paste the **연결 코드** from Deck settings. Save it.
4. Switch between tabs in a focused Whale window. Rules that do not match leave the current preset unchanged.

Manual preset selection pauses automatic switching until **수동 고정 해제 · 자동 전환 재개** in Deck settings, or the optional **자동 프리셋 재개** right-click radial action. Running macros are not stopped by automatic tab switching.

The receiver listens only on `127.0.0.1:18773` and requires the random connection code. If a different program occupies the port, close it or disable this feature. The token is deliberately omitted from Deck JSON exports and is regenerated on import.
