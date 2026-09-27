// Runs in the extension's isolated world; page scripts cannot read picker state.
(() => {
  // A reloaded unpacked extension can leave an obsolete listener in a tab.
  // Replace it on every injection so the current extension context answers.
  if (globalThis.__macroRelayElementPickerListener) {
    try { chrome.runtime.onMessage.removeListener(globalThis.__macroRelayElementPickerListener); }
    catch (_error) { /* The previous extension context may already be invalid. */ }
  }

  const shortText = (element) => {
    if (!element) return "";
    if (element.matches('input[type="password"]')) return "••••••";
    const value = element.matches("input,textarea") ? element.value : element.innerText || element.textContent;
    return String(value || element.getAttribute("aria-label") || element.getAttribute("title") || "")
      .trim().replace(/\s+/g, " ").slice(0, 160);
  };
  const visible = (element) => {
    if (!element) return false;
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.visibility !== "hidden" &&
      style.display !== "none" && box.right > 0 && box.bottom > 0 &&
      box.left < innerWidth && box.top < innerHeight;
  };
  const unique = (selector) => {
    try { return document.querySelectorAll(selector).length === 1; }
    catch (_error) { return false; }
  };
  const interactive = (element) => {
    if (!element) return false;
    if (["html", "body"].includes(element.localName)) return false;
    if (element.hasAttribute("data-name") && element.hasAttribute("title")) return true;
    if (element.matches('button,a[href],input,textarea,select,summary,[role="button"],[role="link"],[role="menuitem"],[role="tab"],[role="option"],[contenteditable="true"]')) return true;
    return element.hasAttribute("onclick") || getComputedStyle(element).cursor === "pointer";
  };
  const namedControl = (element) => element && !["html", "body"].includes(element.localName) &&
    ["data-name", "data-testid", "data-test", "aria-label"].some((name) => element.hasAttribute(name));
  const cssString = (value) => JSON.stringify(String(value));
  const selectorFor = (element) => {
    if (!element || element === document.documentElement) return "";
    if (element.id) {
      const byId = `#${CSS.escape(element.id)}`;
      if (unique(byId)) return byId;
    }
    for (const attribute of ["data-name", "data-testid", "data-test", "name", "aria-label"]) {
      const value = element.getAttribute(attribute);
      if (!value) continue;
      const candidate = `${element.localName}[${attribute}=${cssString(value)}]`;
      if (unique(candidate)) return candidate;
    }
    let current = element;
    const parts = [];
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
      if (current.id) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      let part = current.localName;
      const classes = [...current.classList].filter((name) => !/\d{4,}|^css-|^sc-/i.test(name));
      if (classes.length) {
        const candidate = `${part}.${classes.slice(0, 2).map((name) => CSS.escape(name)).join(".")}`;
        if (unique(candidate)) return candidate;
      }
      const siblings = current.parentElement
        ? [...current.parentElement.children].filter((child) => child.localName === current.localName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      parts.unshift(part);
      current = current.parentElement;
    }
    return parts.join(" > ");
  };
  const details = (element, selector) => {
    let count = 0;
    try { count = document.querySelectorAll(selector).length; }
    catch (_error) { /* invalid selector is reported as no match */ }
    return {selector, count, tag: element?.localName || "", text: shortText(element),
      visible: visible(element), interactive: interactive(element)};
  };
  const frame = () => {
    const host = document.createElement("div");
    host.style.cssText = "all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none";
    const shadow = host.attachShadow({mode: "closed"});
    const outline = document.createElement("div");
    outline.style.cssText = "position:fixed;display:none;border:3px solid #38e7ff;background:#38e7ff22;" +
      "box-sizing:border-box;border-radius:3px;pointer-events:none";
    const hint = document.createElement("div");
    hint.style.cssText = "position:fixed;display:none;background:#111827;color:#fff;padding:5px 8px;" +
      "font:12px sans-serif;border-radius:4px;max-width:320px;white-space:nowrap;overflow:hidden;" +
      "text-overflow:ellipsis;pointer-events:none";
    shadow.append(outline, hint);
    document.documentElement.appendChild(host);
    let marked = null;
    let previous = null;
    const restore = () => {
      if (!marked || !previous) return;
      for (const [name, value, priority] of previous) {
        if (value) marked.style.setProperty(name, value, priority);
        else marked.style.removeProperty(name);
      }
      marked = null;
      previous = null;
    };
    return {host, outline, hint,
      mark(element, broad) {
        if (element === marked) return;
        restore();
        if (!element) return;
        marked = element;
        previous = ["outline", "outline-offset"].map((name) =>
          [name, element.style.getPropertyValue(name), element.style.getPropertyPriority(name)]);
        element.style.setProperty("outline", `3px solid ${broad ? "#fb923c" : "#38e7ff"}`, "important");
        element.style.setProperty("outline-offset", "-3px", "important");
      },
      dispose() { restore(); host.remove(); }
    };
  };
  const draw = (ui, element, label = "") => {
    if (!element || !visible(element)) {
      ui.outline.style.display = ui.hint.style.display = "none";
      ui.mark(null);
      return;
    }
    const box = element.getBoundingClientRect();
    const broad = ["html", "body"].includes(element.localName);
    ui.mark(element, broad);
    ui.outline.style.display = "block";
    ui.outline.style.borderColor = broad ? "#fb923c" : "#38e7ff";
    ui.outline.style.left = `${box.left}px`;
    ui.outline.style.top = `${box.top}px`;
    ui.outline.style.width = `${box.width}px`;
    ui.outline.style.height = `${box.height}px`;
    ui.hint.textContent = label || (broad ? "페이지 배경입니다 · 더 구체적인 요소를 가리켜 주세요" :
      `<${element.localName}> ${shortText(element)}`);
    ui.hint.style.display = "block";
    ui.hint.style.left = `${Math.max(0, box.left)}px`;
    ui.hint.style.top = `${Math.max(0, box.top - 28)}px`;
  };
  const targetAt = (x, y, event) => {
    // The event path preserves an SVG icon's actual ancestry even when a
    // floating toolbar or overlay makes a later elementFromPoint() hit <body>.
    const path = typeof event?.composedPath === "function" ? event.composedPath() : [];
    const stack = document.elementsFromPoint?.(x, y) || [document.elementFromPoint(x, y)];
    const starts = [...path, ...stack].filter((node) => node?.nodeType === Node.ELEMENT_NODE);
    for (const start of starts) {
      for (let current = start; current && current !== document.documentElement; current = current.parentElement) {
        if (namedControl(current)) return current;
      }
    }
    for (const start of starts) {
      for (let current = start; current && current !== document.documentElement; current = current.parentElement) {
        if (interactive(current)) return current;
      }
    }
    if (starts.every((node) => ["html", "body"].includes(node.localName))) {
      // Some floating toolbars briefly redraw between pointermove and pointerdown.
      // In that case hit-testing may report only <body>; recover a small named
      // control at the pointer rather than saving the page background.
      let nearest = null;
      let smallest = 100000;
      for (const candidate of document.querySelectorAll("[data-name]")) {
        const box = candidate.getBoundingClientRect();
        const area = box.width * box.height;
        if (area < smallest && box.left <= x && x < box.right && box.top <= y && y < box.bottom &&
            visible(candidate)) {
          nearest = candidate;
          smallest = area;
        }
      }
      if (nearest) return nearest;
    }
    return starts.find((node) => !["html", "body"].includes(node.localName)) || starts[0] || null;
  };
  const pick = () => new Promise((resolve) => {
    const ui = frame();
    ui.hint.textContent = "요소 선택 준비됨 · 마우스를 올리고 좌클릭하세요 (Esc 취소)";
    ui.hint.style.display = "block";
    ui.hint.style.left = "16px";
    ui.hint.style.top = "16px";
    let hovered = null;
    let settled = false;
    const timeout = setTimeout(() => finish({ok: false, error: "요소 선택 시간이 초과되었습니다."}), 45000);
    const finish = (result, swallowClick = false) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      document.removeEventListener("pointermove", onMove, true);
      document.removeEventListener("pointerdown", onDown, true);
      if (swallowClick) setTimeout(() => document.removeEventListener("click", onClick, true), 300);
      else document.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKey, true);
      ui.dispose();
      resolve(result);
    };
    const onMove = (event) => {
      hovered = targetAt(event.clientX, event.clientY, event);
      draw(ui, hovered);
    };
    const onDown = (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      hovered = targetAt(event.clientX, event.clientY, event);
      if (["html", "body"].includes(hovered?.localName)) {
        draw(ui, hovered, "페이지 배경입니다 · 실제 버튼이나 아이콘 위에서 다시 클릭하세요");
        return;
      }
      const selector = selectorFor(hovered);
      finish(selector ? {ok: true, ...details(hovered, selector)} :
        {ok: false, error: "요소를 선택하지 못했습니다."}, true);
    };
    const onClick = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      finish({ok: false, error: "요소 선택을 취소했습니다."});
    };
    document.addEventListener("pointermove", onMove, true);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
  });
  const probe = async (selector) => {
    let nodes;
    try { nodes = [...document.querySelectorAll(selector)]; }
    catch (error) { return {ok: false, error: `CSS 선택자 오류: ${error.message}`}; }
    const element = nodes.find(visible) || nodes[0];
    if (element) {
      element.scrollIntoView({block: "center", inline: "center", behavior: "instant"});
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const ui = frame();
      draw(ui, element, `${nodes.length}개 일치 · <${element.localName}> ${shortText(element)}`);
      await new Promise((resolve) => setTimeout(resolve, 1500));
      ui.dispose();
    }
    return {ok: true, ...details(element, selector)};
  };
  const pointFor = (element) => {
    const box = element.getBoundingClientRect();
    const left = Math.max(0, box.left);
    const right = Math.min(innerWidth, box.right);
    const top = Math.max(0, box.top);
    const bottom = Math.min(innerHeight, box.bottom);
    if (right <= left || bottom <= top) return null;
    const acceptsHit = (hit) => {
      if (!hit) return false;
      if (hit === element || element.contains(hit)) return true;
      // An icon's SVG/span can use pointer-events:none. The actual trusted
      // mouse event then lands on its small parent toolbar button instead.
      for (let parent = element.parentElement, depth = 0;
           parent && depth < 3 && !["body", "html"].includes(parent.localName);
           parent = parent.parentElement, depth++) {
        if (hit !== parent) continue;
        const parentBox = parent.getBoundingClientRect();
        return parentBox.width <= Math.max(box.width * 4, 80) &&
          parentBox.height <= Math.max(box.height * 4, 80);
      }
      return false;
    };
    for (const yFraction of [0.5, 0.25, 0.75]) {
      for (const xFraction of [0.5, 0.25, 0.75]) {
        const x = left + (right - left) * xFraction;
        const y = top + (bottom - top) * yFraction;
        const hit = document.elementFromPoint(x, y);
        if (acceptsHit(hit)) return {x, y};
      }
    }
    return null;
  };
  const run = async (selector, action, value, testMode = false) => {
    let nodes;
    try { nodes = [...document.querySelectorAll(selector)]; }
    catch (error) { return {ok: false, error: `CSS 선택자 오류: ${error.message}`}; }
    const element = nodes.find(visible) || nodes[0];
    if (!element) return {ok: false, error: "선택자에 맞는 요소가 없습니다."};
    if (["click", "double_click", "hover"].includes(action)) {
      if (["html", "body"].includes(element.localName)) {
        return {ok: false, error: "선택자가 페이지 배경을 가리킵니다. 실제 클릭할 버튼·입력칸을 다시 선택해 주세요."};
      }
      if (["ul", "ol", "nav"].includes(element.localName) && !interactive(element)) {
        return {ok: false, error: "선택자가 목록 전체를 가리킵니다. 목록 안의 실제 항목·버튼에 커서를 올려 다시 추출해 주세요."};
      }
      element.scrollIntoView({block: "center", inline: "center", behavior: "instant"});
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (testMode) {
        const ui = frame();
        draw(ui, element, `동작 테스트 · <${element.localName}> ${shortText(element)}`);
        await new Promise((resolve) => setTimeout(resolve, 300));
        ui.dispose();
      }
      const point = pointFor(element);
      if (!point) {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(
          Math.max(0, Math.min(innerWidth - 1, box.left + box.width / 2)),
          Math.max(0, Math.min(innerHeight - 1, box.top + box.height / 2)));
        const blocker = hit && !["html", "body"].includes(hit.localName)
          ? ` (위쪽 요소: <${hit.localName}>${hit.classList?.length ? "." + [...hit.classList].slice(0, 2).join(".") : ""})` : "";
        return {ok: false, error: `선택한 요소가 화면 밖에 있거나 다른 요소에 가려져 있습니다.${blocker}`};
      }
      return {ok: true, ...details(element, selector), point};
    }
    if (action === "type_text") {
      const content = String(value || "");
      if (element.matches("input,textarea")) {
        const setter = Object.getOwnPropertyDescriptor(
          element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
          "value").set;
        setter.call(element, content);
      } else if (element.isContentEditable) element.textContent = content;
      else return {ok: false, error: "텍스트를 입력할 수 있는 요소가 아닙니다."};
      element.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: content}));
      element.dispatchEvent(new Event("change", {bubbles: true}));
    } else if (action !== "extract_text") return {ok: false, error: "지원하지 않는 브라우저 동작입니다."};
    const info = details(element, selector);
    if (action === "extract_text") {
      info.text = element.matches('input[type="password"]') ? "" :
        String(element.matches("input,textarea") ? element.value : element.innerText || element.textContent || "")
          .trim().slice(0, 4096);
    }
    return {ok: true, ...info};
  };
  const onMessage = (message, _sender, sendResponse) => {
    if (message?.type !== "macrorelay-element") return false;
    const task = message.kind === "pick_element" ? pick() :
      message.kind === "probe_element" ? probe(String(message.selector || "")) :
      run(String(message.selector || ""), String(message.action || ""), message.value,
        message.kind === "test_element_action");
    task.then(sendResponse).catch((error) => sendResponse({ok: false, error: String(error)}));
    return true;
  };
  chrome.runtime.onMessage.addListener(onMessage);
  globalThis.__macroRelayElementPickerListener = onMessage;
})();
