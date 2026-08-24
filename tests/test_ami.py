from __future__ import annotations

import asyncio
import socket
import threading

from asl_transcriber.ami import (
    AmiClient,
    AmiConnectionState,
    AmiError,
    AmiFrameParser,
    PersistentAmiClient,
)


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


def test_ami_frame_parser_handles_fragmented_and_coalesced_frames() -> None:
    parser = AmiFrameParser()

    assert parser.feed(b"Event: RPT_ALI") == []
    frames = parser.feed(
        b"NKS\r\nOutput: first\r\nOutput: second\r\n\r\n"
        b"Response: Success\r\nConn: one\r\nConn: two\r\n\r\n"
    )

    assert len(frames) == 2
    assert frames[0].values("Output") == ["first", "second"]
    assert frames[1].values("Conn") == ["one", "two"]


def test_ami_frame_parser_rejects_partial_frame_on_disconnect() -> None:
    parser = AmiFrameParser()
    parser.feed(b"Response: Success\r\nMessage: incomplete")

    try:
        parser.finish()
    except AmiError as error:
        assert "partial frame" in str(error)
    else:
        raise AssertionError("partial AMI frame was accepted")


def test_persistent_client_routes_concurrent_actions_and_events() -> None:
    async def scenario() -> None:
        received_event = asyncio.Event()
        authenticated = asyncio.Event()
        states: list[AmiConnectionState] = []

        async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"Asterisk Call Manager/5.0\r\n")
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")
            await writer.drain()
            actions = []
            for _ in range(2):
                request = (await reader.readuntil(b"\r\n\r\n")).decode()
                actions.append(
                    next(
                        line.split(": ", 1)[1]
                        for line in request.splitlines()
                        if line.startswith("ActionID")
                    )
                )
            writer.write(
                f"Event: RPT_RXKEYED\r\nNode: 668390\r\nEventValue: 1\r\n\r\n"
                f"Response: Success\r\nActionID: {actions[1]}\r\n\r\n"
                f"Response: Success\r\nActionID: {actions[0]}\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()

        async def on_event(_frame) -> None:
            received_event.set()

        async def on_state(state: AmiConnectionState) -> None:
            states.append(state)
            if state is AmiConnectionState.AUTHENTICATED:
                authenticated.set()

        server_instance = await asyncio.start_server(server, "127.0.0.1", 0)
        port = server_instance.sockets[0].getsockname()[1]
        client = PersistentAmiClient(
            "127.0.0.1",
            port,
            "admin",
            "secret",
            reconnect=False,
            event_callback=on_event,
            state_callback=on_state,
        )
        await client.start()
        await asyncio.wait_for(authenticated.wait(), 1)
        first, second = await asyncio.gather(client.execute("Ping"), client.execute("Ping"))
        await asyncio.wait_for(received_event.wait(), 1)
        await client.stop()
        server_instance.close()
        await server_instance.wait_closed()

        assert first.success and second.success
        assert states[:2] == [AmiConnectionState.CONNECTING, AmiConnectionState.AUTHENTICATED]

    asyncio.run(scenario())


def test_persistent_client_demultiplexes_action_events_from_unsolicited_events() -> None:
    async def scenario() -> None:
        authenticated = asyncio.Event()
        unsolicited: list[str] = []

        async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"Asterisk Call Manager/5.0\r\n")
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")
            await writer.drain()
            request = (await reader.readuntil(b"\r\n\r\n")).decode()
            action_id = next(
                line.split(": ", 1)[1]
                for line in request.splitlines()
                if line.startswith("ActionID")
            )
            writer.write(
                (
                    f"Response: Success\r\nActionID: {action_id}\r\nEventList: start\r\n\r\n"
                    f"Event: Status\r\nActionID: {action_id}\r\nChannel: IAX2/test\r\n\r\n"
                    "Event: FullyBooted\r\nStatus: Fully Booted\r\n\r\n"
                    f"Event: StatusComplete\r\nActionID: {action_id}\r\n"
                    "EventList: Complete\r\n\r\n"
                ).encode()
            )
            await writer.drain()

        async def on_event(frame) -> None:
            unsolicited.append(frame.event or "")

        async def on_state(state: AmiConnectionState) -> None:
            if state is AmiConnectionState.AUTHENTICATED:
                authenticated.set()

        server_instance = await asyncio.start_server(server, "127.0.0.1", 0)
        port = server_instance.sockets[0].getsockname()[1]
        client = PersistentAmiClient(
            "127.0.0.1",
            port,
            "admin",
            "secret",
            reconnect=False,
            event_callback=on_event,
            state_callback=on_state,
        )
        await client.start()
        await asyncio.wait_for(authenticated.wait(), 1)
        result = await client.execute("Status")
        await client.stop()
        server_instance.close()
        await server_instance.wait_closed()

        assert result.success
        assert result.values("Channel") == ["IAX2/test"]
        assert unsolicited == ["FullyBooted"]

    asyncio.run(scenario())


def test_persistent_client_reconnects_and_forces_authenticated_callback() -> None:
    async def scenario() -> None:
        authenticated_count = 0
        reauthenticated = asyncio.Event()

        async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal authenticated_count
            writer.write(b"Asterisk Call Manager/5.0\r\n")
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            authenticated_count += 1
            writer.write(b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")
            await writer.drain()
            if authenticated_count == 1:
                writer.close()
                await writer.wait_closed()
            else:
                await reader.read()

        async def after_authentication() -> None:
            if authenticated_count >= 2:
                reauthenticated.set()

        server_instance = await asyncio.start_server(server, "127.0.0.1", 0)
        port = server_instance.sockets[0].getsockname()[1]
        client = PersistentAmiClient(
            "127.0.0.1",
            port,
            "admin",
            "secret",
            reconnect=True,
            reconnect_max_seconds=0.01,
            random_source=lambda: 0,
            authenticated_callback=after_authentication,
        )
        await client.start()
        await asyncio.wait_for(reauthenticated.wait(), 1)
        await client.stop()
        server_instance.close()
        await server_instance.wait_closed()

        assert authenticated_count >= 2

    asyncio.run(scenario())
