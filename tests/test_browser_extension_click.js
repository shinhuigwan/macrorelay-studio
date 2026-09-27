const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../browser_extension/background.js"), "utf8");
const prefix = source.slice(0, source.indexOf("async function handleStudioElementCommand"));
const calls = [];
const context = {
  navigator: {userAgent: "Whale/4.0"},
  setTimeout,
  chrome: {debugger: {
    async attach(target, version) { calls.push(["attach", target.tabId, version]); },
    async sendCommand(target, method, args) { calls.push(["send", target.tabId, method, args]); },
    async detach(target) { calls.push(["detach", target.tabId]); }
  }}
};
vm.runInNewContext(`${prefix}\nglobalThis.dispatch = dispatchTrustedMouse;`, context);

(async () => {
  await context.dispatch(12, "click", {x: 120, y: 80});
  assert.deepEqual(calls.map((item) => item[0]), ["attach", "send", "send", "detach"]);
  assert.deepEqual(calls.filter((item) => item[0] === "send").map((item) => item[3].type),
    ["mousePressed", "mouseReleased"]);
  assert.equal(calls[1][3].x, 120);
  assert.equal(calls[1][3].y, 80);
  calls.length = 0;
  await context.dispatch(12, "double_click", {x: 5, y: 8});
  assert.deepEqual(calls.filter((item) => item[0] === "send").map((item) => item[3].clickCount),
    [1, 1, 2, 2]);
  assert.equal(calls.at(-1)[0], "detach");
  calls.length = 0;
  let failedRelease = false;
  context.chrome.debugger.sendCommand = async (target, method, args) => {
    calls.push(["send", target.tabId, method, args]);
    if (args.type === "mouseReleased" && !failedRelease) {
      failedRelease = true;
      throw new Error("temporary release failure");
    }
  };
  await assert.rejects(context.dispatch(12, "click", {x: 5, y: 8}), /temporary release failure/);
  assert.deepEqual(calls.filter((item) => item[0] === "send").map((item) => item[3].type),
    ["mousePressed", "mouseReleased", "mouseReleased"]);
  assert.equal(calls.at(-1)[0], "detach");
  calls.length = 0;
  await context.dispatch(12, "hover", {x: 5, y: 8});
  assert.equal(calls.filter((item) => item[0] === "send").length, 1);
  assert.equal(calls.at(-1)[0], "detach");
  const contentSource = fs.readFileSync(path.join(__dirname, "../browser_extension/elements.js"), "utf8");
  const listeners = new Set();
  const contentContext = {chrome: {runtime: {onMessage: {
    addListener(listener) { listeners.add(listener); },
    removeListener(listener) { listeners.delete(listener); }
  }}}};
  vm.runInNewContext(contentSource, contentContext);
  vm.runInNewContext(contentSource, contentContext);
  assert.equal(listeners.size, 1, "reinjecting after extension reload must replace the old listener");
  const listElement = {
    localName: "ul", textContent: "shortcuts", innerText: "shortcuts",
    matches() { return false; }, hasAttribute() { return false; },
    getBoundingClientRect() { return {left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100}; }
  };
  Object.assign(contentContext, {
    innerWidth: 1000, innerHeight: 800,
    getComputedStyle() { return {display: "block", visibility: "visible", cursor: "default"}; },
    document: {querySelectorAll() { return [listElement]; }}
  });
  const contentListener = [...listeners][0];
  const listClick = await new Promise((resolve) => contentListener({
    type: "macrorelay-element", kind: "test_element_action",
    selector: "ul.shortcut_list", action: "click"
  }, {}, resolve));
  assert.equal(listClick.ok, false);
  assert.match(listClick.error, /목록 전체/);
  const toolbarBody = {
    nodeType: 1, localName: "body", parentElement: null,
    hasAttribute() { return false; }, matches() { return false; }
  };
  const toolbarWidget = {
    nodeType: 1, localName: "div", parentElement: toolbarBody,
    hasAttribute() { return false; }, matches() { return false; },
    getBoundingClientRect() { return {left: 5, top: 5, right: 43, bottom: 43, width: 38, height: 38}; }
  };
  const toolbarSpan = {
    nodeType: 1, localName: "span", parentElement: toolbarWidget, id: "", classList: [],
    getAttribute(name) { return name === "data-name" ? "FavoriteToolbarLineToolTrendLine" : null; },
    hasAttribute(name) { return name === "data-name" || name === "title"; }, matches() { return false; },
    contains(node) { return node === toolbarSvg || node === toolbarPath; },
    getBoundingClientRect() { return {left: 10, top: 10, right: 38, bottom: 38, width: 28, height: 28}; }
  };
  const toolbarSvg = {
    nodeType: 1, localName: "svg", parentElement: toolbarSpan,
    hasAttribute() { return false; }, matches() { return false; }
  };
  const toolbarPath = {
    nodeType: 1, localName: "path", parentElement: toolbarSvg,
    hasAttribute() { return false; }, matches() { return false; }
  };
  const pickerContext = {
    Node: {ELEMENT_NODE: 1},
    innerWidth: 1000, innerHeight: 800,
    getComputedStyle() { return {cursor: "pointer", visibility: "visible", display: "block"}; },
    document: {
      documentElement: {localName: "html"},
      elementFromPoint() { return toolbarBody; },
      elementsFromPoint() { return [toolbarBody]; },
      querySelectorAll(selector) {
        if (selector === "[data-name]") return [toolbarSpan];
        return selector === 'span[data-name="FavoriteToolbarLineToolTrendLine"]' ? [toolbarSpan] : [];
      }
    },
    chrome: {runtime: {onMessage: {addListener() {}, removeListener() {}}}}
  };
  vm.runInNewContext(contentSource.replace("chrome.runtime.onMessage.addListener(onMessage);",
    "globalThis.pickerInternals = {targetAt, selectorFor, interactive, pointFor}; chrome.runtime.onMessage.addListener(onMessage);"),
  pickerContext);
  const actualTarget = pickerContext.pickerInternals.targetAt(20, 20, {
    composedPath() { return [toolbarPath, toolbarSvg, toolbarSpan, toolbarBody]; }
  });
  assert.equal(actualTarget, toolbarSpan, "the named TradingView control wins over SVG and body");
  assert.equal(pickerContext.pickerInternals.selectorFor(actualTarget),
    'span[data-name="FavoriteToolbarLineToolTrendLine"]');
  assert.equal(pickerContext.pickerInternals.interactive(toolbarBody), false);
  assert.equal(pickerContext.pickerInternals.interactive(toolbarSpan), true);
  pickerContext.document.elementFromPoint = () => toolbarWidget;
  assert.equal(pickerContext.pickerInternals.pointFor(toolbarSpan)?.x, 24,
    "a small clickable parent receives trusted mouse input for a pointer-events:none span");
  pickerContext.document.elementFromPoint = () => ({localName: "div"});
  assert.equal(pickerContext.pickerInternals.pointFor(toolbarSpan), null,
    "an unrelated covering overlay must still block the click");
  assert.equal(pickerContext.pickerInternals.targetAt(20, 20, {
    composedPath() { return [toolbarBody]; }
  }), toolbarSpan, "small named control is recovered when transient hit-testing returns body");
  const timerStart = source.indexOf("setInterval(() => {");
  const timerEnd = source.indexOf("}, 1000);", timerStart) + "}, 1000);".length;
  let poll;
  const polled = [];
  const pollContext = {
    setInterval(callback) { poll = callback; },
    chrome: {windows: {async getLastFocused() { return {focused: false}; }}},
    checkStudioCommands() { polled.push("studio"); },
    checkRuntimeCommands() { polled.push("runtime"); },
    checkDeckCommands() { polled.push("deck"); }
  };
  vm.runInNewContext(source.slice(timerStart, timerEnd), pollContext);
  await poll();
  assert.deepEqual(polled, ["studio", "runtime"],
    "reselect commands must be polled while Studio is foreground");
  const deckTimerStart = source.indexOf("setInterval(async () => {", timerEnd);
  const deckTimerEnd = source.indexOf("}, 100);", deckTimerStart) + "}, 100);".length;
  assert.ok(deckTimerStart > timerEnd && deckTimerEnd > deckTimerStart,
    "Deck browser commands must use the short-latency poll");
  let deckPoll;
  let browserFocused = false;
  const deckPolled = [];
  vm.runInNewContext(source.slice(deckTimerStart, deckTimerEnd), {
    setInterval(callback, interval) {
      assert.equal(interval, 100);
      deckPoll = callback;
    },
    chrome: {windows: {async getLastFocused() { return {focused: browserFocused}; }}},
    checkDeckCommands() { deckPolled.push("deck"); }
  });
  await deckPoll();
  assert.deepEqual(deckPolled, [], "an unfocused browser should not poll Deck commands");
  browserFocused = true;
  await deckPoll();
  assert.deepEqual(deckPolled, ["deck"], "a focused browser should dispatch Deck commands promptly");
  const existingTabs = [
    {id: 3, windowId: 1, active: true, url: "https://example.com/other", title: "Other"},
    {id: 7, windowId: 1, active: false, url: "https://example.com/login?temporary=1", title: "Login"}
  ];
  const activated = [];
  const sentKinds = [];
  const handlerContext = {
    navigator: {userAgent: "Whale/4.0"}, URL, setTimeout,
    chrome: {
      windows: {
        async getLastFocused() { return {id: 1}; },
        async update(id, options) { activated.push(["window", id, options]); }
      },
      tabs: {
        async query() { return existingTabs; },
        async update(id, options) { activated.push(["tab", id, options]); },
        async sendMessage(id, message) {
          assert.equal(id, 7);
          sentKinds.push(message.kind);
          return {ok: true, selector: "#login", count: 1, tag: "button",
            visible: true, interactive: true, point: {x: 20, y: 40}};
        }
      },
      scripting: {async executeScript({target}) { assert.equal(target.tabId, 7); }},
      debugger: {
        async attach() {}, async sendCommand() {}, async detach() {}
      }
    }
  };
  const handlerPrefix = source.slice(0, source.indexOf("async function checkElementCommands"));
  vm.runInNewContext(`${handlerPrefix}\nglobalThis.handleCommand = handleStudioElementCommand;`, handlerContext);
  const handled = await handlerContext.handleCommand({browser: "whale", page_url: "https://example.com/login",
    kind: "run_element_action", selector: "#login", action: "click"});
  assert.equal(handled.input_method, "browser_debugger");
  assert.equal(handled.url, "https://example.com/login");
  assert.deepEqual(activated.map((item) => item[1]), [7, 1]);
  await handlerContext.handleCommand({browser: "whale", page_url: "https://example.com/login",
    kind: "run_element_action", test_mode: true, selector: "#login", action: "click"});
  assert.deepEqual(sentKinds, ["run_element_action", "test_element_action"]);
  console.log("browser debugger mouse input tests passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
