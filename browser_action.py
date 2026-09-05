import argparse
import json
import os
import sys
import time
import socket
import socketserver
import subprocess
import threading
from typing import Optional

from playwright.sync_api import sync_playwright

os.environ.setdefault("NODE_NO_WARNINGS", "1")


def _browser_hint(browser: str, port: int) -> str:
    name = (browser or "auto").lower()
    if name == "whale":
        return f"웨일을 --remote-debugging-port={port} 옵션으로 실행했는지 확인하세요."
    if name == "edge":
        return f"엣지를 --remote-debugging-port={port} 옵션으로 실행했는지 확인하세요."
    if name == "chrome":
        return f"크롬을 --remote-debugging-port={port} 옵션으로 실행했는지 확인하세요."
    return f"크롬/웨일/엣지를 --remote-debugging-port={port} 옵션으로 실행했는지 확인하세요."


def _connect_over_cdp(playwright, port: int, browser: str):
    url = f"http://127.0.0.1:{port}"
    try:
        return playwright.chromium.connect_over_cdp(url)
    except Exception as exc:
        raise RuntimeError(_browser_hint(browser, port)) from exc


def list_pages(port: int, browser: str) -> None:
    with sync_playwright() as p:
        browser_obj = _connect_over_cdp(p, port, browser)
        pages = []
        for context in browser_obj.contexts:
            for page in context.pages:
                try:
                    title = page.title()
                except Exception:
                    title = ""
                pages.append({"title": title, "url": page.url})
        browser_obj.close()
    print(json.dumps(pages, ensure_ascii=False))


def find_page(browser, title: str, prefer_active: bool = False):
    if prefer_active:
        for context in browser.contexts:
            for page in context.pages:
                try:
                    if page.evaluate("document.hasFocus()"):
                        return page
                except Exception:
                    continue
    if not title:
        for context in browser.contexts:
            if context.pages:
                return context.pages[0]
        return None
    for context in browser.contexts:
        for page in context.pages:
            try:
                if title in page.title():
                    return page
            except Exception:
                continue
    return None


def find_element(page, selector: str, timeout_ms: int, poll_ms: int):
    deadline = time.time() + (timeout_ms / 1000.0)
    poll = max(poll_ms, 50) / 1000.0
    while True:
        element = page.query_selector(selector)
        if element:
            return element
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                element = frame.query_selector(selector)
            except Exception:
                continue
            if element:
                return element
        if timeout_ms <= 0 or time.time() >= deadline:
            return None
        time.sleep(poll)


def _extract_text_from_element(element):
    script = """
    (el) => {
      if (!el) return '';
      const tag = (el.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') {
        return (el.value || '').trim();
      }
      const txt = (el.innerText || el.textContent || '').trim();
      if (txt) return txt;
      try {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
        let node = walker.nextNode();
        while (node) {
          const value = (node.nodeValue || '').trim();
          if (value) return value;
          node = walker.nextNode();
        }
      } catch (err) {}
      try {
        const before = window.getComputedStyle(el, '::before').content;
        if (before && before !== 'none' && before !== 'normal') {
          return String(before).replace(/^["']|["']$/g, '').trim();
        }
        const after = window.getComputedStyle(el, '::after').content;
        if (after && after !== 'none' && after !== 'normal') {
          return String(after).replace(/^["']|["']$/g, '').trim();
        }
      } catch (err) {}
      const attrs = ['aria-label', 'title', 'alt', 'placeholder', 'value', 'data-tooltip'];
      for (const name of attrs) {
        const val = el.getAttribute && el.getAttribute(name);
        if (val && String(val).trim()) return String(val).trim();
      }
      return '';
    }
    """
    try:
        return element.evaluate(script) or ""
    except Exception:
        return ""


def run_action(args: argparse.Namespace):
    with sync_playwright() as p:
        browser_obj = _connect_over_cdp(p, args.port, args.browser)
        page = find_page(browser_obj, args.title or "", bool(args.prefer_active))
        if page is None and args.title:
            page = find_page(browser_obj, "", bool(args.prefer_active))
        if page is None:
            raise RuntimeError("대상 페이지를 찾지 못했습니다.")
        selector = args.selector
        element = find_element(page, selector, args.timeout, args.poll)
        if not element:
            raise RuntimeError("요소를 찾지 못했습니다.")
        action = args.action
        if action == "click":
            element.click()
        elif action == "double_click":
            element.dblclick()
        elif action == "type_text":
            element.fill("")
            element.type(args.value or "")
        elif action == "extract_text":
            return _extract_text_from_element(element)
        elif action == "hover":
            element.hover()
        else:
            raise RuntimeError(f"지원하지 않는 액션: {action}")
        browser_obj.close()


def _emit_extract_text(result, output_path: str) -> None:
    text = result or ""
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)


def pick_selector(args: argparse.Namespace) -> None:
    with sync_playwright() as p:
        browser_obj = _connect_over_cdp(p, args.port, args.browser)
        page = find_page(browser_obj, args.title or "", bool(args.prefer_active))
        if page is None and args.title:
            page = find_page(browser_obj, "", bool(args.prefer_active))
        if page is None:
            raise RuntimeError("대상 페이지를 찾지 못했습니다.")
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            page.evaluate("window.focus && window.focus(); document.body && document.body.focus && document.body.focus();")
        except Exception:
            pass
        page.evaluate(
            """
            () => {
              if (window.__macroPickerCleanup) return;
              const overlay = document.createElement('div');
              const catcher = document.createElement('div');
              overlay.style.position = 'fixed';
              overlay.style.border = '2px solid #0078ff';
              overlay.style.zIndex = '2147483647';
              overlay.style.pointerEvents = 'none';
              overlay.style.boxSizing = 'border-box';
              overlay.style.display = 'none';
              catcher.style.position = 'fixed';
              catcher.style.inset = '0';
              catcher.style.zIndex = '2147483646';
              catcher.style.background = 'transparent';
              catcher.style.cursor = 'crosshair';
              catcher.style.pointerEvents = 'auto';
              catcher.tabIndex = 0;
              document.documentElement.appendChild(overlay);
              document.documentElement.appendChild(catcher);
              catcher.focus();
              const focusTimer = setInterval(() => {
                if (document.activeElement !== catcher) catcher.focus();
              }, 200);
              let lastEl = null;
              let lastPoint = { x: 0, y: 0 };
              const escapeCss = (value) => {
                if (window.CSS && CSS.escape) return CSS.escape(value);
                return value.replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
              };
              const selectorFor = (el) => {
                if (!el || el.nodeType !== 1) return '';
                if (el === document.documentElement || el === document.body) return '';
                if (el.id) return '#' + escapeCss(el.id);
                const parts = [];
                while (el && el.nodeType === 1 && el !== document.documentElement) {
                  let name = el.tagName.toLowerCase();
                  if (el.classList && el.classList.length) {
                    name += '.' + Array.from(el.classList).map(escapeCss).join('.');
                  }
                  let sibling = el;
                  let idx = 1;
                  while ((sibling = sibling.previousElementSibling)) {
                    if (sibling.tagName === el.tagName) idx += 1;
                  }
                  name += `:nth-of-type(${idx})`;
                  parts.unshift(name);
                  el = el.parentElement;
                }
                return parts.join(' > ');
              };
              const hasText = (el) => {
                if (!el) return false;
                const tag = (el.tagName || '').toLowerCase();
                let text = '';
                if (tag === 'input' || tag === 'textarea') {
                  text = el.value || '';
                } else {
                  text = (el.innerText || el.textContent || '');
                }
                return text.trim().length > 0;
              };
              const updateOverlay = (el) => {
                if (!el || el === catcher || el === overlay || el === document.documentElement || el === document.body) {
                  overlay.style.display = 'none';
                  return;
                }
                if (!hasText(el)) {
                  overlay.style.display = 'none';
                  return;
                }
                const rect = el.getBoundingClientRect();
                overlay.style.display = 'block';
                overlay.style.left = rect.left + 'px';
                overlay.style.top = rect.top + 'px';
                overlay.style.width = rect.width + 'px';
                overlay.style.height = rect.height + 'px';
              };
              const pickElement = (x, y) => {
                let el = document.elementFromPoint(x, y);
                if (el === catcher || el === overlay) {
                  catcher.style.pointerEvents = 'none';
                  el = document.elementFromPoint(x, y);
                  catcher.style.pointerEvents = 'auto';
                }
                return el;
              };
              const updateFromPoint = (x, y) => {
                lastPoint = { x, y };
                let el = pickElement(x, y);
                let textEl = null;
                const caret = document.caretRangeFromPoint
                  ? document.caretRangeFromPoint(x, y)
                  : (document.caretPositionFromPoint ? document.caretPositionFromPoint(x, y) : null);
                if (caret) {
                  const node = caret.startContainer || caret.offsetNode;
                  if (node) {
                    textEl = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
                  }
                }
                if (!textEl || !hasText(textEl)) {
                  while (el && !hasText(el)) {
                    el = el.parentElement;
                  }
                  textEl = el;
                }
                if (textEl && hasText(textEl)) {
                  lastEl = textEl;
                  updateOverlay(textEl);
                } else {
                  overlay.style.display = 'none';
                }
              };
              const finalize = (el) => {
                const sel = selectorFor(el || lastEl);
                window.__macroPickerPicked = sel || '';
                window.__macroPickerDone = true;
              };
              const onMove = (ev) => {
                updateFromPoint(ev.clientX, ev.clientY);
              };
              const onClick = (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                updateFromPoint(ev.clientX, ev.clientY);
                finalize(lastEl);
              };
              const onKey = (ev) => {
                const key = ev.key || '';
                const code = ev.code || '';
                const keyCode = ev.keyCode || ev.which || 0;
                if (key === 'Enter' || code === 'Enter' || keyCode === 13) {
                  ev.preventDefault();
                  ev.stopPropagation();
                  const el = pickElement(lastPoint.x, lastPoint.y);
                  finalize(el);
                } else if (key === 'Escape') {
                  ev.preventDefault();
                  ev.stopPropagation();
                  window.__macroPickerPicked = '';
                  window.__macroPickerDone = true;
                }
              };
              catcher.addEventListener('mousemove', onMove, true);
              catcher.addEventListener('click', onClick, true);
              catcher.addEventListener('mousedown', onClick, true);
              catcher.addEventListener('keydown', onKey, true);
              catcher.addEventListener('keyup', onKey, true);
              window.addEventListener('keydown', onKey, true);
              window.addEventListener('keyup', onKey, true);
              document.addEventListener('keydown', onKey, true);
              document.addEventListener('keyup', onKey, true);
              document.documentElement.addEventListener('keydown', onKey, true);
              document.documentElement.addEventListener('keyup', onKey, true);
              window.__macroPickerCleanup = () => {
                catcher.removeEventListener('mousemove', onMove, true);
                catcher.removeEventListener('click', onClick, true);
                catcher.removeEventListener('mousedown', onClick, true);
                catcher.removeEventListener('keydown', onKey, true);
                catcher.removeEventListener('keyup', onKey, true);
                window.removeEventListener('keydown', onKey, true);
                window.removeEventListener('keyup', onKey, true);
                document.removeEventListener('keydown', onKey, true);
                document.removeEventListener('keyup', onKey, true);
                document.documentElement.removeEventListener('keydown', onKey, true);
                document.documentElement.removeEventListener('keyup', onKey, true);
                clearInterval(focusTimer);
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                if (catcher.parentNode) catcher.parentNode.removeChild(catcher);
                delete window.__macroPickerPicked;
                delete window.__macroPickerDone;
                delete window.__macroPickerCleanup;
              };
            }
            """
        )
        timeout_ms = max(args.timeout, 1000)
        if args.timeout <= 0:
            while True:
                done = page.evaluate("Boolean(window.__macroPickerDone)")
                if done:
                    break
                time.sleep(0.05)
        else:
            start = time.time()
            while True:
                done = page.evaluate("Boolean(window.__macroPickerDone)")
                if done:
                    break
                if (time.time() - start) * 1000 >= timeout_ms:
                    page.evaluate("window.__macroPickerCleanup && window.__macroPickerCleanup()")
                    raise RuntimeError("요소 선택 시간이 초과되었습니다.")
                time.sleep(0.05)
        selector = page.evaluate("window.__macroPickerPicked || ''")
        page.evaluate("window.__macroPickerCleanup && window.__macroPickerCleanup()")
        browser_obj.close()
    selector = selector or ""
    if not selector:
        raise RuntimeError("요소 선택이 취소되었습니다.")
    print(selector)


class BrowserSession:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._port = None
        self._thread_id = None
        self._browser_name = "auto"

    def set_browser(self, browser_name: str) -> None:
        self._browser_name = (browser_name or "auto").lower()

    def ensure(self, port: int, browser_name: Optional[str] = None):
        current_id = threading.get_ident()
        if self._thread_id is not None and self._thread_id != current_id:
            self.close()
        browser_name = (browser_name or self._browser_name or "auto").lower()
        if self._browser and self._port == port:
            self._thread_id = current_id
            try:
                if hasattr(self._browser, "is_connected") and not self._browser.is_connected():
                    self.close()
                elif not self._browser.contexts:
                    self.close()
                else:
                    return self._browser
            except Exception:
                self.close()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if not self._playwright:
            self._playwright = sync_playwright().start()
        try:
            self._browser = _connect_over_cdp(self._playwright, port, browser_name)
        except Exception as exc:
            if "different thread" not in str(exc):
                raise
            self.close()
            self._playwright = sync_playwright().start()
            self._browser = _connect_over_cdp(self._playwright, port, browser_name)
        self._port = port
        self._thread_id = current_id
        return self._browser

    def close(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def list_pages(self, port: int, browser_name: Optional[str] = None):
        browser = self.ensure(port, browser_name)
        pages = []
        for context in browser.contexts:
            for page in context.pages:
                try:
                    title = page.title()
                except Exception:
                    title = ""
                pages.append({"title": title, "url": page.url})
        return pages

    def run_action(self, args: argparse.Namespace) -> None:
        try:
            return self._run_action_once(args)
        except Exception as exc:
            message = str(exc)
            if "different thread" not in message:
                raise
            self.close()
            return self._run_action_once(args)

    def _run_action_once(self, args: argparse.Namespace) -> None:
        browser = self.ensure(args.port, getattr(args, "browser", None))
        page = find_page(browser, args.title or "", bool(getattr(args, "prefer_active", False)))
        if page is None and args.title:
            page = find_page(browser, "", bool(getattr(args, "prefer_active", False)))
        if page is None:
            raise RuntimeError("대상 페이지를 찾지 못했습니다.")
        selector = args.selector
        element = find_element(page, selector, args.timeout, args.poll)
        if not element:
            raise RuntimeError("요소를 찾지 못했습니다.")
        action = args.action
        if action == "click":
            element.click()
        elif action == "double_click":
            element.dblclick()
        elif action == "type_text":
            element.fill("")
            element.type(args.value or "")
        elif action == "extract_text":
            return _extract_text_from_element(element)
        elif action == "hover":
            element.hover()
        else:
            raise RuntimeError(f"지원하지 않는 액션: {action}")


def run_server(args: argparse.Namespace) -> None:
    session = BrowserSession()
    session.set_browser(getattr(args, "browser", "auto"))

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5.0)
            data = b""
            while True:
                try:
                    chunk = self.request.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
            try:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = data.decode("cp949")
                    except UnicodeDecodeError:
                        text = data.decode("utf-8", errors="replace")
                payload = json.loads(text)
                cmd = payload.get("cmd")
                if cmd == "ping":
                    reply = {"ok": True}
                elif cmd == "shutdown":
                    reply = {"ok": True}
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                elif cmd == "list":
                    browser_name = payload.get("browser") or session._browser_name
                    result = session.list_pages(payload.get("port", 9222), browser_name)
                    reply = {"ok": True, "result": result}
                elif cmd == "action":
                    if payload.get("browser"):
                        session.set_browser(payload.get("browser"))
                    params = argparse.Namespace(**payload)
                    result = session.run_action(params)
                    reply = {"ok": True, "result": result}
                elif cmd == "action_to_file":
                    output_path = str(payload.get("output") or "")
                    if output_path and not os.path.isabs(output_path):
                        output_path = os.path.join(os.path.dirname(__file__), output_path)
                    params = argparse.Namespace(**payload)
                    result = session.run_action(params)
                    if output_path:
                        with open(output_path, "w", encoding="utf-8") as handle:
                            handle.write("" if result is None else str(result))
                    reply = {"ok": True, "result": ""}
                else:
                    reply = {"ok": False, "error": "unknown command"}
            except Exception as exc:
                reply = {"ok": False, "error": str(exc)}
            self.request.sendall(json.dumps(reply).encode("utf-8"))

    class SimpleServer(socketserver.TCPServer):
        allow_reuse_address = True

    with SimpleServer(("127.0.0.1", args.server_port), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            session.close()


def run_client(args: argparse.Namespace):
    payload = {
        "cmd": "action",
        "port": args.port,
        "title": args.title,
        "selector": args.selector,
        "action": args.action,
        "value": args.value,
        "timeout": args.timeout,
        "poll": args.poll,
        "prefer_active": bool(args.prefer_active),
        "browser": args.browser,
    }
    def send_request():
        with socket.create_connection(("127.0.0.1", args.server_port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            sock.sendall(json.dumps(payload).encode("utf-8"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            reply = b"".join(chunks).decode("utf-8", errors="replace")
        data = json.loads(reply or "{}")
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "server error")
        return data.get("result")

    try:
        return send_request()
    except Exception:
        if not _start_server(args):
            return None
        # Server startup (Playwright + CDP attach) can take a couple seconds on first run.
        for _ in range(60):
            try:
                return send_request()
            except Exception:
                time.sleep(0.1)
        return None


def send_shutdown(args: argparse.Namespace) -> bool:
    payload = {"cmd": "shutdown"}
    try:
        with socket.create_connection(("127.0.0.1", args.server_port), timeout=1.5) as sock:
            sock.sendall(json.dumps(payload).encode("utf-8"))
            reply = sock.recv(4096).decode("utf-8")
        data = json.loads(reply or "{}")
        return bool(data.get("ok"))
    except Exception:
        return False


def _start_server(args: argparse.Namespace) -> bool:
    script_path = os.path.abspath(__file__)
    cmd = [
        sys.executable,
        script_path,
        "--server",
        f"--port={args.port}",
        f"--server-port={args.server_port}",
        f"--browser={args.browser}",
    ]
    try:
        creationflags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=creationflags)
        return True
    except Exception:
        return False

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--title", default="")
    parser.add_argument("--title-file", default="")
    parser.add_argument("--selector", default="")
    parser.add_argument("--selector-file", default="")
    parser.add_argument("--action", default="click")
    parser.add_argument("--value", default="")
    parser.add_argument("--timeout", type=int, default=2000)
    parser.add_argument("--poll", type=int, default=50)
    parser.add_argument("--prefer-active", action="store_true")
    parser.add_argument("--server-port", type=int, default=9233)
    parser.add_argument("--browser", default="auto", choices=["auto", "chrome", "whale", "edge"])
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--shutdown", action="store_true")
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--pick", action="store_true")
    args = parser.parse_args()
    if not args.title and args.title_file:
        try:
            with open(args.title_file, "r", encoding="utf-8-sig") as handle:
                args.title = handle.read().strip()
        except Exception:
            args.title = ""
    if not args.selector and args.selector_file:
        try:
            with open(args.selector_file, "r", encoding="utf-8-sig") as handle:
                args.selector = handle.read().strip()
        except Exception:
            args.selector = ""
    fallback = not args.no_fallback
    log_handle = None
    try:
        if args.log:
            log_handle = open(args.log, "w", encoding="utf-8")
            sys.stdout = log_handle
            sys.stderr = log_handle
        if args.server:
            run_server(args)
        elif args.shutdown:
            if not send_shutdown(args):
                raise RuntimeError("server not running")
        elif args.list:
            list_pages(args.port, args.browser)
        elif args.pick:
            pick_selector(args)
        elif args.client:
            result = run_client(args)
            if result is None:
                if fallback:
                    result = run_action(args)
                else:
                    raise RuntimeError("browser_action server not running")
            if args.action == "extract_text":
                _emit_extract_text(result, args.output)
        else:
            result = run_action(args)
            if args.action == "extract_text":
                _emit_extract_text(result, args.output)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        if log_handle:
            log_handle.flush()
            log_handle.close()


if __name__ == "__main__":
    main()
