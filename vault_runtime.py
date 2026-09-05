from __future__ import annotations

"""Minimal standalone DPAPI vault reader used by exported macros.

This file intentionally depends only on the Python standard library so a
portable export does not need to copy the full Studio package. It writes the
decrypted value only to the caller-provided temporary path; the AHK runtime
deletes that file immediately after reading it.
"""

import argparse
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is required")
    source, source_buffer = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer


def read_secret(root: Path, name: str) -> str:
    vault_root = Path(root) / ".vault"
    payload = json.loads((vault_root / "index.json").read_text(encoding="utf-8-sig"))
    filename = payload.get(str(name or "").strip()) if isinstance(payload, dict) else None
    if not filename:
        raise KeyError(name)
    return _unprotect((vault_root / str(filename)).read_bytes()).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = read_secret(args.root, args.name)
        args.out.write_text(value, encoding="utf-8-sig")
    except Exception:
        try:
            args.out.unlink(missing_ok=True)
        except OSError:
            pass
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
