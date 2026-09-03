from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]


class QrzError(RuntimeError):
    pass


@dataclass(frozen=True)
class QrzCallsign:
    callsign: str
    name: str | None = None
    location: str | None = None
    image_url: str | None = None
    profile_url: str | None = None
    status: str = "found"

    def serialize(self) -> dict[str, str | None]:
        return asdict(self)


def _child_text(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    for child in element:
        if child.tag.rsplit("}", 1)[-1].casefold() == name.casefold():
            value = (child.text or "").strip()
            return value or None
    return None


def _first_element(root: ET.Element, name: str) -> ET.Element | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].casefold() == name.casefold():
            return element
    return None


def _safe_image_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


class QrzClient:
    """Small QRZ XML client with session reuse and callsign-result caching."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = "https://xmldata.qrz.com/xml/current/",
        timeout_seconds: float = 10.0,
        cache_seconds: float = 86400.0,
        agent: str = "repeater-scribe",
        max_response_bytes: int = 1_000_000,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.agent = agent
        self.max_response_bytes = max_response_bytes
        self.opener = opener
        self._session_key: str | None = None
        self._cache: dict[str, tuple[float, QrzCallsign]] = {}
        self._lock = threading.Lock()

    def lookup(self, callsign: str) -> QrzCallsign:
        normalized = callsign.strip().upper()
        with self._lock:
            cached = self._cache.get(normalized)
            if cached is not None and monotonic() - cached[0] < self.cache_seconds:
                return cached[1]

            if self._session_key is None:
                self._login()
            root = self._request({"s": self._session_key or "", "callsign": normalized})
            session = _first_element(root, "Session")
            if _child_text(session, "Key") is None:
                self._session_key = None
                self._login()
                root = self._request({"s": self._session_key or "", "callsign": normalized})
                session = _first_element(root, "Session")

            record = self._parse_lookup(root, normalized)
            self._cache[normalized] = (monotonic(), record)
            session_key = _child_text(session, "Key")
            if session_key:
                self._session_key = session_key
            return record

    def cached_callsigns(self) -> tuple[str, ...]:
        """Return successfully validated callsigns without performing network requests."""
        with self._lock:
            return tuple(
                callsign
                for callsign, (_, record) in self._cache.items()
                if record.status == "found"
            )

    def cached_status(self, callsign: str) -> str | None:
        """Return a cached validation status without triggering a QRZ request."""
        with self._lock:
            cached = self._cache.get(callsign.strip().upper())
            return cached[1].status if cached is not None else None

    def _login(self) -> None:
        root = self._request(
            {"username": self.username, "password": self.password, "agent": self.agent}
        )
        session = _first_element(root, "Session")
        key = _child_text(session, "Key")
        if not key:
            raise QrzError(_child_text(session, "Error") or "QRZ login did not return a session key")
        self._session_key = key

    def _request(self, parameters: dict[str, str]) -> ET.Element:
        request = Request(
            self.base_url,
            data=urlencode(parameters).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = bytearray()
                while len(payload) <= self.max_response_bytes:
                    chunk = response.read(min(65_536, self.max_response_bytes + 1 - len(payload)))
                    if not chunk:
                        return ET.fromstring(bytes(payload))
                    payload.extend(chunk)
            raise QrzError("QRZ response exceeds the configured size limit")
        except QrzError:
            raise
        except (OSError, ET.ParseError, DefusedXmlException) as error:
            raise QrzError(f"QRZ request failed: {error}") from error

    @staticmethod
    def _parse_lookup(root: ET.Element, requested: str) -> QrzCallsign:
        callsign = _first_element(root, "Callsign")
        session = _first_element(root, "Session")
        if callsign is None:
            error = _child_text(session, "Error") or "Callsign not found"
            if "not found" in error.casefold():
                return QrzCallsign(
                    callsign=requested,
                    profile_url=f"https://www.qrz.com/db/{requested}",
                    status="not_found",
                )
            raise QrzError(error)

        resolved = (_child_text(callsign, "call") or requested).upper()
        name = _child_text(callsign, "name_fmt")
        if not name:
            name = " ".join(
                part
                for part in (_child_text(callsign, "fname"), _child_text(callsign, "name"))
                if part
            ) or None
        location = ", ".join(
            dict.fromkeys(
                part
                for part in (
                    _child_text(callsign, "addr2"),
                    _child_text(callsign, "state"),
                    _child_text(callsign, "country") or _child_text(callsign, "land"),
                )
                if part
            )
        ) or None
        return QrzCallsign(
            callsign=resolved,
            name=name,
            location=location,
            image_url=_safe_image_url(_child_text(callsign, "image")),
            profile_url=f"https://www.qrz.com/db/{resolved}",
        )
