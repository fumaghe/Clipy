"""Protezione a riposo dei segreti salvati nel DB locale.

Windows: DPAPI (CryptProtectData, ambito utente corrente) → i segreti nel file `clipy.db` sono inutilizzabili se il file viene
copiato su un'altra macchina/utente. Valori cifrati: prefisso `dpapi:` + base64. Valori in chiaro (installazioni V1, Linux/macOS,
cloud con secret manager) restano leggibili: la migrazione è trasparente e non perde nulla (vedi `protect_all`).

`CLIPY_SECRET_PROTECTION`: `auto` (default: DPAPI su Windows, nulla altrove) · `dpapi` · `none`.
NON TESTATO su Windows reale dallo sviluppo: la logica (formato, migrazione, fallback) è coperta da test con un backend finto."""
from __future__ import annotations

import base64
import os
import sys
from typing import Protocol

PREFIX = "dpapi:"


class Backend(Protocol):
    def encrypt(self, data: bytes) -> bytes: ...
    def decrypt(self, data: bytes) -> bytes: ...


class DpapiBackend:
    """Wrapper ctypes su crypt32 (nessuna dipendenza esterna)."""

    def _blob(self, data: bytes):
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB

    def _call(self, fn_name: str, data: bytes) -> bytes:
        import ctypes

        blob_in, keep, DATA_BLOB = self._blob(data)  # noqa: N806
        out = DATA_BLOB()
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        fn = getattr(crypt32, fn_name)
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(out)) if fn_name == "CryptUnprotectData" else \
            fn(ctypes.byref(blob_in), "Clipy", None, None, None, 0, ctypes.byref(out))
        if not ok:
            raise OSError(f"{fn_name} fallita (codice {ctypes.GetLastError()})")  # type: ignore[attr-defined]
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)
            _ = keep

    def encrypt(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def decrypt(self, data: bytes) -> bytes:
        return self._call("CryptUnprotectData", data)


_backend_override: Backend | None = None


def set_backend_for_tests(b: Backend | None) -> None:
    global _backend_override
    _backend_override = b


def mode() -> str:
    m = os.environ.get("CLIPY_SECRET_PROTECTION", "auto").lower()
    if m == "auto":
        return "dpapi" if (sys.platform == "win32" or _backend_override is not None) else "none"
    return m if m in ("dpapi", "none") else "none"


def _backend() -> Backend:
    if _backend_override is not None:
        return _backend_override
    if sys.platform != "win32":
        raise RuntimeError("DPAPI disponibile solo su Windows")
    return DpapiBackend()


def is_protected(stored: str) -> bool:
    return stored.startswith(PREFIX)


def protect(value: str) -> str:
    if not value or mode() != "dpapi":
        return value
    return PREFIX + base64.b64encode(_backend().encrypt(value.encode("utf-8"))).decode("ascii")


class Unreadable(Exception):
    """Segreto cifrato con DPAPI non decifrabile qui (altro utente/macchina): va reinserito."""


def unprotect(stored: str) -> str:
    if not is_protected(stored):
        return stored
    try:
        return _backend().decrypt(base64.b64decode(stored[len(PREFIX):])).decode("utf-8")
    except Exception as exc:
        raise Unreadable(f"segreto non decifrabile su questo utente/macchina ({type(exc).__name__})") from exc
