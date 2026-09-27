// Only the focused browser window's active tab is sent. No browsing history is stored.
let lastSent = "";
let generation = 0;
let lastObservedAt = 0;
let checkingCommands = false;
const checkingElementPorts = new Set();

function browserKind() {
  const brands = (navigator.userAgentData?.brands || []).map((item) => item.brand).join(" ");
  const identity = `${navigator.userAgent} ${brands}`.toLowerCase();
  if (identity.includes("whale")) return "whale";
  if (identity.includes("edg/") || identity.includes("microsoft edge")) return "edge";
  return "chrome";
}

async function dispatchTrustedMouse(tabId, action, point) {
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    throw new Error("클릭할 요소가 화면에 보이지 않습니다. 선택자를 다시 검사해 주세요.");
  }
  const target = {tabId};
  try {
    await chrome.debugger.attach(target, "1.3");
  } catch (error) {
    throw new Error(`브라우저 실제 클릭 연결에 실패했습니다. 확장앱의 디버거 권한과 개발자 도구 상태를 확인해 주세요. (${error})`);
  }
  try {
    const position = {x: point.x, y: point.y, button: "left", pointerType: "mouse"};
    if (action === "hover") {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
        ...position, type: "mouseMoved", buttons: 0
      });
      return;
    }
    const count = action === "double_click" ? 2 : 1;
    for (let clickCount = 1; clickCount <= count; clickCount++) {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
        ...position, type: "mousePressed", buttons: 1, clickCount
      });
      try {
        await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
          ...position, type: "mouseReleased", buttons: 0, clickCount
        });
      } catch (error) {
        // A failed release must not leave a browser-side mouse button held.
        try {
          await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
            ...position, type: "mouseReleased", buttons: 0, clickCount
          });
        } catch (_retryError) { /* The tab may have navigated or detached. */ }
        throw error;
      }
      if (clickCount < count) await new Promise((resolve) => setTimeout(resolve, 70));
    }
  } finally {
    try { await chrome.debugger.detach(target); } catch (_error) { /* Navigation can detach it. */ }
  }
}

async function handleStudioElementCommand(command) {
  if (command.browser && command.browser !== browserKind()) {
    throw new Error("선택한 브라우저의 확장앱이 아닙니다.");
  }
  const browserWindow = await chrome.windows.getLastFocused();
  const tabs = await chrome.tabs.query({});
  let tab = tabs.find((item) => item.windowId === browserWindow?.id && item.active);
  if (command.page_url) {
    const target = new URL(command.page_url);
    const sameSite = tabs.filter((item) => {
      try { return new URL(item.url).hostname === target.hostname; }
      catch (_error) { return false; }
    });
    const samePath = sameSite.filter((item) => {
      const current = new URL(item.url);
      return current.origin === target.origin && current.pathname === target.pathname;
    });
    tab = samePath.find((item) => item.id === tab?.id) || samePath[0] ||
      sameSite.find((item) => item.id === tab?.id) || sameSite[0];
  }
  if (!tab || !/^https?:\/\//i.test(tab.url || "")) {
    throw new Error("선택한 브라우저의 웹페이지 탭을 찾지 못했습니다.");
  }
  await chrome.tabs.update(tab.id, {active: true});
  await chrome.windows.update(tab.windowId, {focused: true});
  await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ["elements.js"]});
  const result = await chrome.tabs.sendMessage(tab.id, {
    type: "macrorelay-element",
    kind: command.test_mode ? "test_element_action" : command.kind,
    selector: command.selector || "",
    action: command.action || "", value: command.value || ""
  });
  if (!result?.ok) throw new Error(result?.error || "브라우저 요소 응답이 없습니다.");
  if (["test_element_action", "run_element_action"].includes(command.kind) &&
      ["click", "double_click", "hover"].includes(command.action)) {
    await dispatchTrustedMouse(tab.id, command.action, result.point);
    result.input_method = "browser_debugger";
  }
  const page = new URL(tab.url);
  const safeUrl = `${page.protocol}//${page.hostname}${page.pathname}`;
  return {...result, url: safeUrl, title: tab.title || "", browser: browserKind()};
}

async function checkElementCommands(port) {
  if (checkingElementPorts.has(port)) return;
  checkingElementPorts.add(port);
  try {
    const {token = ""} = await chrome.storage.local.get("token");
    if (!token) return;
    const headers = {"X-MacroRelay-Token": token};
    const base = `http://127.0.0.1:${port}`;
    const response = await fetch(`${base}/next-command?browser=${encodeURIComponent(browserKind())}`, {headers});
    if (response.status !== 200) return;
    const command = await response.json();
    let result;
    try {
      if (!["pick_element", "probe_element", "test_element_action", "run_element_action"].includes(command.kind)) {
        throw new Error("Unsupported Studio browser command");
      }
      result = {id: command.id, status: "ok", result: await handleStudioElementCommand(command)};
    } catch (error) {
      result = {id: command.id, status: "error", error: String(error)};
    }
    await fetch(`${base}/command-result`, {
      method: "POST", headers: {...headers, "Content-Type": "application/json"},
      body: JSON.stringify(result)
    });
  } catch (_error) {
    // Studio's local receiver may be closed.
  } finally {
    checkingElementPorts.delete(port);
  }
}

function checkStudioCommands() { return checkElementCommands(18774); }
function checkRuntimeCommands() { return checkElementCommands(18775); }

async function activateRequestedTab(command) {
  const target = new URL(command.url);
  const tabs = await chrome.tabs.query({});
  const domain = (host) => host.replace(/^www\./i, "");
  const matches = tabs.filter((tab) => {
    try {
      const current = new URL(tab.url || "");
      const currentDomain = domain(current.hostname);
      const targetDomain = domain(target.hostname);
      if (currentDomain !== targetDomain &&
          !currentDomain.endsWith(`.${targetDomain}`)) return false;
      if (command.match === "domain") return true;
      return current.origin === target.origin &&
        current.pathname.replace(/\/$/, "") === target.pathname.replace(/\/$/, "");
    } catch (_error) { return false; }
  });
  const whaleWindow = await chrome.windows.getLastFocused();
  const existing = matches.find((tab) => tab.windowId === whaleWindow?.id && tab.active) ||
    matches.find((tab) => tab.windowId === whaleWindow?.id) || matches[0];
  let tab;
  if (existing) {
    tab = await chrome.tabs.update(existing.id, command.kind === "navigate_tab"
      ? {url: command.url, active: true} : {active: true});
    await chrome.windows.update(existing.windowId, {focused: true});
  } else {
    if (command.kind === "navigate_tab") {
      throw new Error("이 사이트의 열린 탭을 찾지 못했습니다. 새 탭은 열지 않았습니다.");
    }
    tab = await chrome.tabs.create({url: command.url, active: true,
      ...(whaleWindow?.id >= 0 ? {windowId: whaleWindow.id} : {})});
    await chrome.windows.update(tab.windowId, {focused: true});
  }
  await reportFocusedTab(true);
  return tab.url || command.url;
}

async function checkDeckCommands() {
  if (checkingCommands) return;
  checkingCommands = true;
  try {
    const {token = ""} = await chrome.storage.local.get("token");
    if (!token) return;
    const headers = {"X-MacroRelay-Token": token};
    const response = await fetch("http://127.0.0.1:18773/next-command", {headers});
    if (response.status !== 200) return;
    const command = await response.json();
    let result = {id: command.id, status: "ok", url: ""};
    try {
      if (command.kind !== "activate_tab" && command.kind !== "navigate_tab") {
        throw new Error("Unsupported Deck command");
      }
      result.url = await activateRequestedTab(command);
    } catch (error) {
      result = {id: command.id, status: "error", error: String(error)};
    }
    await fetch("http://127.0.0.1:18773/command-result", {
      method: "POST", headers: {...headers, "Content-Type": "application/json"},
      body: JSON.stringify(result)
    });
  } catch (_error) {
    // Local Deck receiver may be closed.
  } finally {
    checkingCommands = false;
  }
}

async function reportFocusedTab(force = false) {
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
    if (!force && key === lastSent) return;
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
    setTimeout(() => { void checkDeckCommands(); }, 200);
    setTimeout(() => { void checkStudioCommands(); }, 200);
    setTimeout(() => { void checkRuntimeCommands(); }, 200);
  }
});
chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId === 0) void reportFocusedTab();
});
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === "local") { lastSent = ""; void reportFocusedTab(); }
});

// The Deck can restart while the same browser tab remains focused. Re-send
// the active tab occasionally so the new local receiver can recover state.
chrome.alarms.create("deck-active-tab-sync", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "deck-active-tab-sync") {
    void reportFocusedTab(true); void checkDeckCommands(); void checkStudioCommands(); void checkRuntimeCommands();
  }
});
chrome.runtime.onStartup.addListener(() => { void reportFocusedTab(true); void checkDeckCommands(); void checkStudioCommands(); void checkRuntimeCommands(); });
chrome.runtime.onInstalled.addListener(() => { void reportFocusedTab(true); void checkDeckCommands(); void checkStudioCommands(); void checkRuntimeCommands(); });
// Studio may be foreground when its user clicks "pick again". Keep accepting
// its localhost commands even then; the command itself focuses the target tab.
setInterval(() => {
  void checkStudioCommands();
  void checkRuntimeCommands();
}, 1000);
// Deck taps should not wait up to a full second for the next browser poll.
setInterval(async () => {
  try {
    if (!(await chrome.windows.getLastFocused()).focused) return;
    void checkDeckCommands();
  } catch (_error) { /* The browser may be closing. */ }
}, 100);
void reportFocusedTab(true);
void checkDeckCommands();
void checkStudioCommands();
void checkRuntimeCommands();
