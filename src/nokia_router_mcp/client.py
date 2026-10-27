"""HTTP client for the Nokia WiFi Beacon / gateway web UI ("web_whw" Angular app).

The protocol here was derived from the router's own web UI JavaScript
(firmware code version 2501.20.03.A17 on a Beacon G6):

Login (standard gateways):
  1. POST login_web_app.cgi?nonce   body: userName=<user>
       -> {nonce, randomKey, pubkey, ...}
  2. Build   userhash=<user>&RandomKeyhash=<randomKey>&response=<urlenc(password)>
             &nonce=<b64url(nonce)>&enckey=<b64url(k)>&enciv=<b64url(iv)>&nohash=1&hPassword=undefined
     and encrypt it: AES-128-CBC with a random key/iv, the key/iv string wrapped
     with the router's RSA public key (PKCS#1 v1.5)  ->  encrypted=1&ct=..&ck=..
  3. POST login_web_app.cgi  -> {result: 0, token, sid, ...}; session cookie is HttpOnly.

Authenticated requests:
  * GET  *_web_app.cgi         (cookie only)
  * POST *_web_app.cgi         body + "&csrf_token=<token>", then encrypted with the pubkey.

Some values (e.g. WiFi pre-shared keys) come back AES-encrypted with the
enckey/enciv the client chose at login.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class RouterError(Exception):
    pass


class LoginError(RouterError):
    pass


# --------------------------------------------------------------------------- crypto helpers
# These mirror `crypto_page` in the web UI's scripts bundle.


def _escape(s: str) -> str:
    """crypto_page.base64url_escape: + -> -, / -> _, = -> ."""
    return s.replace("+", "-").replace("/", "_").replace("=", ".")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64url_nopad(b: bytes) -> str:
    """sjcl.codec.base64url.fromBits: url alphabet, no padding."""
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sha256_b64(x: str, k: str) -> str:
    """crypto_page.sha256(X, k) = base64(sha256(X + ':' + k))."""
    return _b64(hashlib.sha256(f"{x}:{k}".encode()).digest())


def _sha256url(x: str, k: str) -> str:
    return _escape(_sha256_b64(x, k))


def _encode_uri_component(s: str) -> str:
    return quote(s, safe="-_.!~*'()")


def _load_pubkey(pem: str):
    pem = pem.strip().replace("\\n", "\n")
    if "BEGIN" not in pem:
        body = re.sub(r"\s+", "", pem)
        lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        pem = f"-----BEGIN PUBLIC KEY-----\n{lines}\n-----END PUBLIC KEY-----\n"
    return serialization.load_pem_public_key(pem.encode())


def _aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(data) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def encrypt_post_data(pubkey_pem: str, plaintext: str) -> str:
    """crypto_page.encrypt_post_data -> 'encrypted=1&ct=..&ck=..'."""
    key, iv = os.urandom(16), os.urandom(16)
    ct = _b64url_nopad(_aes_cbc_encrypt(key, iv, plaintext.encode()))
    pub = _load_pubkey(pubkey_pem)
    ck_raw = pub.encrypt(f"{_b64(key)} {_b64(iv)}".encode(), asym_padding.PKCS1v15())
    return f"encrypted=1&ct={ct}&ck={_escape(_b64(ck_raw))}"


# --------------------------------------------------------------------------- response parsing


def parse_json(text: str) -> Any:
    """The router emits JSON with raw control characters and sometimes trailing commas."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", text), strict=False)
    except json.JSONDecodeError:
        return {"_raw": text[:4000]}


# --------------------------------------------------------------------------- client


@dataclass
class RouterConfig:
    url: str = "http://192.168.1.1"
    username: str = ""
    password: str = ""
    login_mode: str = "standard"  # standard | fwa
    timeout: float = 60.0
    verify_tls: bool = False

    @classmethod
    def from_env(cls) -> "RouterConfig":
        return cls(
            url=os.environ.get("NOKIA_ROUTER_URL", "http://192.168.1.1").rstrip("/"),
            username=os.environ.get("NOKIA_ROUTER_USERNAME", ""),
            password=os.environ.get("NOKIA_ROUTER_PASSWORD", ""),
            login_mode=os.environ.get("NOKIA_ROUTER_LOGIN_MODE", "standard").lower(),
            timeout=float(os.environ.get("NOKIA_ROUTER_TIMEOUT", "60")),
            verify_tls=os.environ.get("NOKIA_ROUTER_VERIFY_TLS", "0") == "1",
        )


class NokiaRouter:
    # Re-validate the session with check_expire after this much idle time.
    IDLE_RECHECK_SECONDS = 120

    def __init__(self, config: RouterConfig):
        self.cfg = config
        self.http = requests.Session()
        self.http.verify = config.verify_tls
        self.http.headers.update(
            {
                "User-Agent": "nokia-router-mcp/0.2.0",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.token: str | None = None
        self.pubkey: str = ""
        self._enc_key: bytes | None = None
        self._enc_iv: bytes | None = None
        self._last_ok = 0.0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ low level

    def _url(self, endpoint: str) -> str:
        return f"{self.cfg.url}/{endpoint.lstrip('/')}"

    def _raw_get(self, endpoint: str) -> requests.Response:
        return self.http.get(self._url(endpoint), timeout=self.cfg.timeout)

    def _raw_post(self, endpoint: str, body: str) -> requests.Response:
        return self.http.post(
            self._url(endpoint),
            data=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.cfg.timeout,
            allow_redirects=False,
        )

    # ------------------------------------------------------------------ auth

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    def login(self) -> dict:
        with self._lock:
            if not self.cfg.username or not self.cfg.password:
                raise LoginError(
                    "NOKIA_ROUTER_USERNAME / NOKIA_ROUTER_PASSWORD are not set "
                    "(put them in the environment or a .env file)."
                )
            self.http.cookies.clear()
            self.token = None

            r = self._raw_post("login_web_app.cgi?nonce", f"userName={self.cfg.username}")
            r.raise_for_status()
            nonce_data = parse_json(r.text) or {}
            nonce = nonce_data.get("nonce")
            if not nonce:
                raise LoginError(f"Router did not return a login nonce: {str(nonce_data)[:300]}")
            self.pubkey = nonce_data.get("pubkey", "") or ""
            self._enc_key, self._enc_iv = os.urandom(16), os.urandom(16)
            enc = f"&enckey={_escape(_b64(self._enc_key))}&enciv={_escape(_b64(self._enc_iv))}"

            if self.cfg.login_mode == "fwa":
                body = self._fwa_login_body(nonce_data) + enc
            else:
                body = (
                    f"userhash={self.cfg.username}"
                    f"&RandomKeyhash={nonce_data.get('randomKey', '')}"
                    f"&response={_encode_uri_component(self.cfg.password)}"
                    f"&nonce={_escape(nonce)}"
                    f"{enc}&nohash=1&hPassword=undefined"
                )
                body = encrypt_post_data(self.pubkey, body)

            r = self._raw_post("login_web_app.cgi", body)
            r.raise_for_status()
            data = parse_json(r.text) or {}
            if data.get("result") == 0 and data.get("token") and data.get("sid"):
                self.token = str(data["token"])
                self._last_ok = time.time()
                return {
                    "ok": True,
                    "admin": bool(data.get("is_ctc_admin")),
                    "password_change_requested": data.get("ModifyPassword"),
                }
            self._enc_key = self._enc_iv = None
            detail = {k: data.get(k) for k in ("result", "ret", "failurecount", "tip", "error_msg", "is_hacker") if k in data}
            raise LoginError(f"Login rejected by router: {detail or str(data)[:300]}")

    def _fwa_login_body(self, n: dict) -> str:
        """Salted/iterated login used by FastMile (FWA) models. Untested on real hardware."""
        user, pw, nonce = self.cfg.username, self.cfg.password, n["nonce"]
        userhash = _sha256url(user, nonce)
        r = self._raw_post("login_web_app.cgi?salt", f"userhash={userhash}&nonce={_escape(nonce)}")
        r.raise_for_status()
        salt_data = parse_json(r.text) or {}
        alati = (salt_data.get("alati") or (salt_data.get("data") or {}).get("alati") or "")
        iterations = int(n.get("iterations") or 0)
        h = hashlib.sha256((alati + pw).encode()).hexdigest() if iterations >= 1 else alati + pw
        for _ in range(1, iterations):
            h = hashlib.sha256(bytes.fromhex(h)).hexdigest()
        response = _sha256url(_sha256_b64(user, h.lower()), nonce)
        rk = _sha256url(n.get("randomKey", ""), nonce)
        return f"userhash={userhash}&RandomKeyhash={rk}&response={response}&nonce={_escape(nonce)}"

    def logout(self) -> None:
        with self._lock:
            if self.token:
                try:
                    self._raw_get("login_web_app.cgi?out")
                except requests.RequestException:
                    pass
            self.token = None
            self.http.cookies.clear()

    def session_alive(self) -> bool:
        if not self.token:
            return False
        try:
            r = self._raw_get("check_expire_web_app.cgi")
        except requests.RequestException:
            return False
        if r.status_code != 200:
            return False
        data = parse_json(r.text)
        return isinstance(data, dict) and data.get("expired", "no") == "no"

    def ensure_session(self) -> None:
        with self._lock:
            if self.token and time.time() - self._last_ok < self.IDLE_RECHECK_SECONDS:
                return
            if not self.session_alive():
                self.login()
            self._last_ok = time.time()

    # ------------------------------------------------------------------ public API

    @contextmanager
    def transaction(self):
        """Serialize an entire read/validate/write/verify sequence in this process."""
        with self._lock:
            yield

    @staticmethod
    def _looks_unauthenticated(r: requests.Response) -> bool:
        if r.status_code in (401, 403):
            return True
        head = r.text.lstrip()[:200].lower()
        return head.startswith("<!doctype html") or head.startswith("<html")

    def get(self, endpoint: str) -> Any:
        """Authenticated GET of a *_web_app.cgi endpoint, returns parsed JSON."""
        with self._lock:
            self.ensure_session()
            r = self._raw_get(endpoint)
            if self._looks_unauthenticated(r):
                self.login()
                r = self._raw_get(endpoint)
            r.raise_for_status()
            self._last_ok = time.time()
            return parse_json(r.text)

    def post(self, endpoint: str, payload: str | None = None) -> Any:
        """POST once; callers must reconcile ambiguous results by reading state."""
        with self._lock:
            self.ensure_session()
            r = self._raw_post(endpoint, self._csrf_body(payload))
            if 300 <= r.status_code < 400:
                raise RouterError("Write returned a redirect; read state before retrying.")
            if self._looks_unauthenticated(r):
                self.token = None
                raise RouterError("Write returned an authentication page/error; read state before retrying.")
            r.raise_for_status()
            self._last_ok = time.time()
            return parse_json(r.text)

    def get_bytes(self, endpoint: str) -> bytes:
        """Authenticated GET returning the raw body (config export)."""
        with self._lock:
            self.ensure_session()
            r = self._raw_get(endpoint)
            if self._looks_unauthenticated(r):
                self.login()
                r = self._raw_get(endpoint)
            r.raise_for_status()
            self._last_ok = time.time()
            return r.content

    def post_file(self, endpoint: str, filename: str, content: bytes) -> Any:
        """The web UI's POST_FORM_DATA: multipart with csrf_token + 'filename' file field (not encrypted)."""
        with self._lock:
            self.ensure_session()
            r = self.http.post(
                self._url(endpoint),
                data={"csrf_token": self.token},
                files={"filename": (filename, content, "application/octet-stream")},
                timeout=max(self.cfg.timeout, 180),
            )
            r.raise_for_status()
            self._last_ok = time.time()
            return parse_json(r.text)

    def _csrf_body(self, payload: str | None) -> str:
        body = f"{payload}&csrf_token={self.token}" if payload else f"csrf_token={self.token}"
        return encrypt_post_data(self.pubkey, body) if self.pubkey else body

    def decrypt_value(self, value: str) -> str:
        """crypto_page.aes_decrypt: values encrypted with the enckey/enciv sent at login."""
        if not value or not self._enc_key or not self._enc_iv:
            return value
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4))
            return _aes_cbc_decrypt(self._enc_key, self._enc_iv, raw).decode()
        except Exception:
            return value
