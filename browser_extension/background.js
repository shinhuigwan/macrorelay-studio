// Only the focused browser window's active tab is sent. No browsing history is stored.
let lastSent = "";
let generation = 0;
let lastObservedAt = 0;

async function reportFocusedTab() {
  const current = ++generation;
  try {
    const window = await chrome.windows.getLastFocused();
    if (!window || !window.focused) return;
    const [tab] = await chrome.tabs.query({active: true, windowId: window.id});
    if (current !== generation || !tab || !/^https?:\/\//i.test(tab.url || "")) return;
    const {token = ""} = await chrome.storage.local.get("token");
    if (!token || current !== generation) return;
    const parsed = new URL(tab.url);
    const safeUrl = `${parsed.protocol}//${parsed.hostname}${parsed.pathname}`;
    const key = `${window.id}:${tab.id}:${safeUrl}`;
    if (key === lastSent) return;
    const observedAt = Math.max(Date.now(), lastObservedAt + 1);
    lastObservedAt = observedAt;
    const response = await fetch("http://127.0.0.1:18773/active-tab", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-MacroRelay-Token": token},
      body: JSON.stringify({url: safeUrl, observedAt})
    });
    if (response.ok && current === generation) lastSent = key;
  } catch (_error) {
    // Deck may be closed or automatic switching disabled; remain silent.
  }
}

chrome.tabs.onActivated.addListener(() => { void reportFocusedTab(); });
chrome.tabs.onUpdated.addListener((_tabId, change) => {
  if (change.url) void reportFocusedTab();
});
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId !== chrome.windows.WINDOW_ID_NONE) {
    lastSent = "";
    void reportFocusedTab();
  }
});
chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId === 0) void reportFocusedTab();
});
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === "local") { lastSent = ""; void reportFocusedTab(); }
});
