from __future__ import annotations

from io import BytesIO
from urllib.parse import parse_qs

import pytest

from asl_transcriber.qrz import QrzClient, QrzError


class Response(BytesIO):
    pass


class ChunkedResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks[0]
        if size < 0 or len(chunk) <= size:
            return self.chunks.pop(0)
        self.chunks[0] = chunk[size:]
        return chunk[:size]


def test_qrz_client_logs_in_once_parses_location_and_caches_lookup() -> None:
    requests: list[dict[str, list[str]]] = []

    def opener(request, **_kwargs):
        parameters = parse_qs(request.data.decode())
        requests.append(parameters)
        if "username" in parameters:
            return Response(
                b'<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>session-key</Key>'
                b"</Session></QRZDatabase>"
            )
        return Response(
            b'<QRZDatabase xmlns="http://xmldata.qrz.com"><Callsign><call>KM7GHS</call>'
            b"<name_fmt>Sam Radio</name_fmt><addr2>Mesa</addr2><state>AZ</state>"
            b"<country>United States</country><image>https://files.qrz.com/k/km7ghs/photo.jpg</image>"
            b"</Callsign><Session><Key>session-key</Key></Session></QRZDatabase>"
        )

    client = QrzClient("user", "secret", opener=opener)

    first = client.lookup("km7ghs")
    second = client.lookup("KM7GHS")

    assert first == second
    assert first.location == "Mesa, AZ, United States"
    assert first.image_url == "https://files.qrz.com/k/km7ghs/photo.jpg"
    assert first.profile_url == "https://www.qrz.com/db/KM7GHS"
    assert client.cached_callsigns() == ("KM7GHS",)
    assert client.cached_status("km7ghs") == "found"
    assert client.cached_status("N0NONE") is None
    assert len(requests) == 2
    assert requests[0]["password"] == ["secret"]


def test_qrz_client_returns_not_found_record() -> None:
    responses = iter(
        [
            b"<QRZDatabase><Session><Key>key</Key></Session></QRZDatabase>",
            (
                b"<QRZDatabase><Session><Error>Not found: N0NONE</Error><Key>key</Key>"
                b"</Session></QRZDatabase>"
            ),
        ]
    )
    client = QrzClient("user", "secret", opener=lambda *_args, **_kwargs: Response(next(responses)))

    result = client.lookup("N0NONE")

    assert result.status == "not_found"
    assert result.callsign == "N0NONE"
    assert client.cached_status("N0NONE") == "not_found"


def test_qrz_response_size_is_bounded_across_chunks() -> None:
    payload = b"<QRZDatabase><Session><Key>key</Key></Session></QRZDatabase>"
    exact = QrzClient("user", "secret", max_response_bytes=len(payload), opener=lambda *_args, **_kwargs: ChunkedResponse([payload[:10], payload[10:]]))
    assert exact._request({"username": "user"}).tag == "QRZDatabase"
    oversized = QrzClient("user", "secret", max_response_bytes=10, opener=lambda *_args, **_kwargs: ChunkedResponse([b"x" * 6, b"x" * 6]))
    with pytest.raises(QrzError, match="size limit"):
        oversized._request({"username": "user"})


@pytest.mark.parametrize(
    "payload",
    [
        b"<QRZDatabase>",
        b"<?xml version='1.0'?><!DOCTYPE a [<!ENTITY x 'value'>]><QRZDatabase>&x;</QRZDatabase>",
    ],
)
def test_qrz_malformed_or_hostile_xml_is_controlled(payload: bytes) -> None:
    client = QrzClient("user", "secret", opener=lambda *_args, **_kwargs: ChunkedResponse([payload]))
    with pytest.raises(QrzError, match="QRZ request failed"):
        client._request({"username": "user"})
