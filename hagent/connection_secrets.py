"""Protect connector credentials at rest; never place them in model prompts."""
import base64
import ctypes
import json
import os
from pathlib import Path

from hagent import db


def _windows_protect(value: bytes, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), "Hagent connections", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise ValueError("Windows could not unlock connection credentials for this user")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("HAGENT_CONNECTION_KEY")
    if not key:
        path = Path(db.CONFIG_PATH).parent / "connections.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                os.chmod(path, 0o600)
                handle.write(Fernet.generate_key())
        except FileExistsError:
            pass
        key = path.read_bytes()
    return Fernet(key)


def protect(values: dict) -> str:
    raw = json.dumps(values).encode()
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_windows_protect(raw)).decode()
    return "fernet:" + _fernet().encrypt(raw).decode()


def reveal(value: str) -> dict:
    if not value:
        return {}
    try:
        scheme, encrypted = value.split(":", 1)
        if scheme == "dpapi" and os.name == "nt":
            raw = _windows_protect(base64.b64decode(encrypted), decrypt=True)
        elif scheme == "fernet":
            raw = _fernet().decrypt(encrypted.encode())
        else:
            raise ValueError("Unsupported credential format")
        return json.loads(raw)
    except Exception as exc:
        raise ValueError("Connection credentials could not be unlocked; reconnect this account") from exc
