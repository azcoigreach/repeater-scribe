from __future__ import annotations

import socket
import threading

from asl_transcriber.ami import AmiClient


def test_ami_client_logs_in_and_executes_command() -> None:
    server, client = socket.socketpair()
    received: list[str] = []

    def fake_server() -> None:
        with server:
            server.sendall(b"Asterisk Call Manager/5.0\r\n")
            login = server.recv(4096).decode()
            received.append(login)
            server.sendall(b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")
            action = server.recv(4096).decode()
            received.append(action)
            server.sendall(b"Response: Success\r\nMessage: Command output follows\r\n\r\n")

    thread = threading.Thread(target=fake_server)
    thread.start()

    def factory(address: tuple[str, int], timeout: float) -> socket.socket:
        return client

    response = AmiClient("node", 5038, "admin", "secret", socket_factory=factory).command(
        "rpt fun 668390 *3"
    )
    thread.join()

    assert response.success
    assert "Action: Login" in received[0]
    assert "Secret: secret" in received[0]
    assert "Action: Command" in received[1]
    assert "Command: rpt fun 668390 *3" in received[1]
