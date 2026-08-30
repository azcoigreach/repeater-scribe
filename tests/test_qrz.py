from __future__ import annotations

from io import BytesIO
from urllib.parse import parse_qs

from asl_transcriber.qrz import QrzClient


class Response(BytesIO):
    pass


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
