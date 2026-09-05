from __future__ import annotations

import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class CredentialVault:
    """User-scoped Windows DPAPI storage; macro JSON contains names only."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / ".vault"
        self.index_path = self.root / "index.json"

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    @staticmethod
    def _protect(data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("Windows DPAPI는 Windows에서만 사용할 수 있습니다.")
        source, source_buffer = CredentialVault._blob(data)
        output = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), "MacroRelay Vault", None, None, None, 0x01, ctypes.byref(output)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
            del source_buffer

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("Windows DPAPI는 Windows에서만 사용할 수 있습니다.")
        source, source_buffer = CredentialVault._blob(data)
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

    def _index(self) -> dict[str, str]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return {str(key): str(value) for key, value in payload.items()} if isinstance(payload, dict) else {}

    def _save_index(self, payload: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.index_path)

    @staticmethod
    def _filename(name: str) -> str:
        return hashlib.sha256(name.encode("utf-8")).hexdigest() + ".bin"

    def set(self, name: str, value: str) -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("보관함 이름을 입력하세요.")
        self.root.mkdir(parents=True, exist_ok=True)
        filename = self._filename(key)
        encrypted = self._protect(str(value).encode("utf-8"))
        temporary = (self.root / filename).with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.root / filename)
        index = self._index()
        index[key] = filename
        self._save_index(index)

    def get(self, name: str) -> str:
        key = str(name or "").strip()
        filename = self._index().get(key)
        if not filename:
            raise KeyError(key)
        return self._unprotect((self.root / filename).read_bytes()).decode("utf-8")

    def delete(self, name: str) -> None:
        index = self._index()
        filename = index.pop(str(name or "").strip(), "")
        if filename:
            (self.root / filename).unlink(missing_ok=True)
            self._save_index(index)

    def names(self) -> list[str]:
        return sorted(self._index(), key=str.casefold)
