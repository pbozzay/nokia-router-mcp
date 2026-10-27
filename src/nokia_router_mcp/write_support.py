"""Validation and verification shared by structured router writes."""
from __future__ import annotations

import ipaddress
import re
import time
from typing import Any, Callable
from urllib.parse import urlencode, quote

import requests

from .client import RouterError


def hostname(value: str) -> str:
    value = value.strip().rstrip(".").lower()
    if not value or len(value) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("Use a DNS hostname with letters, digits and hyphens (no wildcard).")
    return value


def ipv4(value: str) -> str:
    ip = ipaddress.IPv4Address(value)
    if ip.is_multicast or ip.is_unspecified or int(ip) == 0xffffffff:
        raise ValueError("Use a unicast IPv4 address.")
    return str(ip)


def object_id(value: Any) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise RouterError("Router returned an invalid object identifier.")
    return str(value)


def request(endpoint: str, fields: dict | None = None) -> dict:
    return {"endpoint": endpoint, "form_body": urlencode(fields, quote_via=quote) if fields else None}


def result(status: str, before: Any, after: Any, requests_: list[dict], **extra) -> dict:
    return {"status": status, "before": before, "after": after, "requests": requests_, **extra}


def apply_once(router, req: dict, read: Callable, satisfied: Callable, delays: tuple[float, ...]) -> dict:
    """Never replay a POST, including after timeout. State is the success criterion."""
    response = None
    error = None
    try:
        response = router.post(req["endpoint"], req["form_body"])
    except (requests.RequestException, RouterError) as exc:
        error = type(exc).__name__
    rejected = False
    if isinstance(response, dict):
        code = response.get("result", response.get("ret"))
        rejected = code is not None and code not in (0, "0")
    after = None
    read_ok = False
    for delay in (0, *delays):
        if delay:
            time.sleep(delay)
        try:
            after = read()
            read_ok = True
            if satisfied(after):
                return {"status": "applied", "after": after}
        except (requests.RequestException, RouterError) as exc:
            read_ok = False
            error = type(exc).__name__
    status = "rejected" if rejected and read_ok else "unknown" if error else "unverified"
    return {"status": status, "after": after if read_ok else None,
            "state_read": read_ok, "error": error,
            "message": "Desired state was not verified. Inspect current state before another write."}
