#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UIA click helper for inactive windows (best-effort)."""

from __future__ import annotations

import argparse
import json
import sys


def _result(ok: bool, message: str = "") -> int:
    payload = {"ok": ok, "message": message}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0 if ok else 1


def _click_uiautomation(x: int, y: int) -> None:
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(1.0)
    ctrl = auto.ControlFromPoint(auto.Point(x, y))
    if not ctrl:
        raise RuntimeError("uiautomation: control not found at point")
    ctrl.Click()


def _click_pywinauto(x: int, y: int) -> None:
    from pywinauto import Desktop

    ctrl = Desktop(backend="uia").from_point(x, y)
    if not ctrl:
        raise RuntimeError("pywinauto: control not found at point")
    ctrl.click_input()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--hwnd", type=str, default="")
    args = parser.parse_args()

    x = args.x
    y = args.y

    try:
        _click_uiautomation(x, y)
        return _result(True, "uiautomation:click")
    except Exception as exc:
        uia_error = str(exc)

    try:
        _click_pywinauto(x, y)
        return _result(True, "pywinauto:click")
    except Exception as exc:
        pyw_error = str(exc)

    return _result(False, f"uia failed: {uia_error} | pywinauto failed: {pyw_error}")


if __name__ == "__main__":
    raise SystemExit(main())
