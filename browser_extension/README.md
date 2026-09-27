# Whale URL-based Deck preset switching

This optional extension reports only the focused tab's scheme, hostname, and path to the locally running QuickSlot Deck. Query strings, fragments, and browsing history are not sent or stored.

1. In QuickSlot Deck, open **환경 설정 → 프리셋 자동 전환**. Add site rules such as `youtube.com`, `naver.com`, or `tradingview.com/chart`, select their Deck presets, and enable automatic switching. Ordinary application rules such as `obs64.exe` work without this browser extension.
2. In Whale, open extension management, enable developer mode, and load this `browser_extension` directory as an unpacked extension.
3. Open this extension's options and paste the **연결 코드** from Deck settings. Save it.
4. Switch between tabs in a focused Whale window. Rules that do not match leave the current preset unchanged. After updating this unpacked extension, click **Reload** in Whale's extension manager. The focused tab is also re-sent periodically so a restarted Deck can recover without a tab change.

The default Deck mode also has six site shortcuts on its third row. A shortcut first looks for a matching Whale tab (domain for Naver, YouTube, Jstris and Google; exact path for TradingView and ChatGPT). It focuses an existing tab or opens a new tab in the current Whale window, then confirms the matching Deck preset. These shortcuts need this extension and its saved connection code even if automatic preset switching is disabled. If the extension does not respond, Deck reports a quiet inline failure without opening a duplicate tab.

Manual preset selection pauses automatic switching only until the next window or tab change, and this pause is cleared when Deck starts again. Every non-default preset uses its first tile as a Home action that returns to the default preset; existing tiles are shifted without being replaced. Running macros are not stopped by automatic tab switching.

The receiver listens only on `127.0.0.1:18773` and requires the random connection code. If a different program occupies the port, close it or disable this feature. The token is deliberately omitted from Deck JSON exports and is regenerated on import.

## Studio browser elements

Studio's **브라우저 요소** quick-add and detail editor can use this same extension in Whale, Chrome, or Edge. Install and reload the unpacked extension in each browser you want to control, then save the same local connection code in its options. The extension needs scripting and website permissions to outline the hovered element, extract a CSS selector on left-click, and highlight selector matches during **선택자 검사**. Esc cancels picking. Inspection does not click the website, while **실제 동작 테스트** does execute the selected action in the already-open tab. Studio shows the match count, element tag, visibility, and a short text sample; password-field values are masked. A selected page's scheme, host, and path are saved with the node, not its query string or fragment. The local Studio receiver uses `127.0.0.1:18774` and macro execution fallback uses `127.0.0.1:18775`, both with the same token.

Click, double-click, and hover use the browser debugger's trusted input events instead of synthetic `element.click()` events. This requires the extension's **debugger** permission; reloading the updated extension can show a new permission warning and attaching briefly displays the browser's debugging notice. The extension detaches immediately after each input. It does not create a new browser tab. If DevTools or another debugger already owns that tab, Studio reports an error rather than claiming the click succeeded. Browser-internal pages and sites where extensions are blocked cannot be inspected. If the selector points at the page background (`body` or `html`), choose a more specific visible element; nested iframe content may need a different selector or capture method.
