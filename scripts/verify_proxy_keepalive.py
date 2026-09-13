"""Disposable Caddy/Uvicorn idle-reuse experiment; no application data or controls.

Run with the Caddy binary matching compose.internet.yml:
  python scripts/verify_proxy_keepalive.py --caddy /path/to/caddy --output /tmp/proxy-check

Synthetic Event/Favorites paths isolate HTTP transport from database/dependency work.
The baseline is diagnostic, not required to fail in every finite run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import uvicorn
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
PATHS = [
    "/api/v1/sessions/fixture",
    "/api/v1/sessions/fixture/markers?limit=25",
    "/api/v1/sessions/fixture/recordings?limit=25",
    "/api/v1/sessions/fixture/checkins?limit=25",
    "/api/v1/sessions/fixture/detected?limit=25",
    "/api/v1/nodes/100/favorites",
]


async def app(scope, receive, send):
    body = json.dumps({"items": [], "peer": scope["client"]}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())
    ]})
    await send({"type": "http.response.body", "body": body})


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def exercise(origin, rounds, concurrency):
    counts = Counter()
    failures = []
    async with httpx.AsyncClient(
        base_url=origin, trust_env=False, verify=False, timeout=10,
        limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
    ) as client:
        # Direct evidence that the configured proxy retires an idle connection
        # before the application's five-second timeout (no UI involved).
        first = (await client.get(PATHS[0])).json()["peer"]
        await asyncio.sleep(2.5)
        second = (await client.get(PATHS[0])).json()["peer"]

        async def read(index):
            path = PATHS[index % len(PATHS)]
            response = await client.get(path)
            counts[response.status_code] += 1
            if response.status_code != 200:
                failures.append({"time": time.time(), "path": path, "status": response.status_code})

        for round_index in range(rounds):
            # Probe both sides of Uvicorn's deadline, including exactly 5s.
            if round_index:
                await asyncio.sleep((4.99, 5.0, 5.01)[round_index % 3])
            await asyncio.gather(*(read(index) for index in range(concurrency)))
    return {"statuses": dict(counts), "failures": failures, "reused_after_2_5s": first == second}


async def exercise_browser(origin, rounds, concurrency):
    counts = Counter()
    failures = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(ignore_https_errors=True)
            await page.goto(origin)
            first = await page.evaluate("async path => (await (await fetch(path)).json()).peer", PATHS[0])
            await asyncio.sleep(2.5)
            second = await page.evaluate("async path => (await (await fetch(path)).json()).peer", PATHS[0])
            for round_index in range(rounds):
                if round_index:
                    await asyncio.sleep((4.99, 5.0, 5.01)[round_index % 3])
                responses = await page.evaluate("""async ({paths, concurrency}) => Promise.all(
                    Array.from({length: concurrency}, async (_, index) => {
                        const path = paths[index % paths.length];
                        const response = await fetch(path, {cache: 'no-store', headers: {
                            Accept: 'application/json', 'Content-Type': 'application/json'
                        }});
                        await response.text();
                        return {path, status: response.status, time: Date.now() / 1000};
                    }))""", {"paths": PATHS, "concurrency": concurrency})
                for response in responses:
                    counts[response["status"]] += 1
                    if response["status"] != 200:
                        failures.append(response)
        finally:
            await browser.close()
    return {"statuses": dict(counts), "failures": failures, "reused_after_2_5s": first == second}


def phase(args, name, upstream):
    port = free_port()
    config = (ROOT / "deploy/Caddyfile").read_text().replace(
        "{$ASLT_PUBLIC_HOST}", f"https://127.0.0.1:{port}"
    ).replace("repeater-scribe:8080", f"127.0.0.1:{upstream}")
    config = config.replace(
        "\tencode zstd gzip",
        f"\ttls {args.output / 'cert.pem'} {args.output / 'key.pem'}\n\tencode zstd gzip",
    )
    if name == "baseline":
        config = config.replace("keepalive 2s", "keepalive 120s")
    config = "{\n admin off\n auto_https off\n persist_config off\n}\n" + config
    config_path = args.output / f"{name}.Caddyfile"
    config_path.write_text(config)
    environment = dict(os.environ, XDG_CONFIG_HOME=str(args.output), XDG_DATA_HOME=str(args.output))
    with (args.output / f"{name}-caddy.log").open("w") as log:
        process = subprocess.Popen(
            [args.caddy, "run", "--config", str(config_path), "--adapter", "caddyfile"],
            stdout=log, stderr=log, env=environment,
        )
        try:
            wait_ready(f"https://127.0.0.1:{port}", process)
            started = time.monotonic()
            exercise_client = exercise_browser if args.client == "chromium" else exercise
            result = asyncio.run(exercise_client(
                f"https://127.0.0.1:{port}", args.rounds, args.concurrency
            ))
            result["duration_seconds"] = round(time.monotonic() - started, 2)
            return result
        finally:
            process.terminate()
            process.wait(timeout=10)


def wait_ready(origin, process):
    with httpx.Client(trust_env=False, verify=False) as client:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Test process exited before readiness; inspect output logs")
            try:
                if client.get(origin).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise RuntimeError("Test process did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--client", choices=["chromium", "http1"], default="chromium")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(args.output / "key.pem"), "-out", str(args.output / "cert.pem"),
        "-days", "1", "-subj", "/CN=127.0.0.1",
    ], check=True, capture_output=True)
    upstream = free_port()
    with (args.output / "uvicorn.log").open("w") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "verify_proxy_keepalive:app",
            "--app-dir", str(Path(__file__).parent), "--host", "127.0.0.1", "--port", str(upstream),
            "--lifespan", "off", "--timeout-keep-alive", "5", "--no-server-header",
            # Synthetic diagnostic only: retain the actual TCP peer port instead
            # of replacing it with the forwarded browser address/port zero.
            "--no-proxy-headers",
        ], stdout=log, stderr=log)
        try:
            wait_ready(f"http://127.0.0.1:{upstream}", process)
            results = {"uvicorn": uvicorn.__version__, "caddy": subprocess.check_output(
                [args.caddy, "version"], text=True
            ).strip(), "rounds": args.rounds, "concurrency": args.concurrency, "client": args.client}
            for name in ("baseline", "corrected"):
                results[name] = phase(args, name, upstream)
                print(json.dumps({name: results[name]}), flush=True)
                (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            assert not results["corrected"]["reused_after_2_5s"], results
            assert not results["corrected"]["failures"], results
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
