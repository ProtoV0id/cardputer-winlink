#!/usr/bin/env python3
"""Exercise a Linux tncd binary with local sockets only; no Bluetooth or RF."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def reserve():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def main():
    executable = str(Path(sys.argv[1]).resolve())
    assert subprocess.check_output([executable, "version"], text=True).strip() == "tncd 2.1.0-pocketlink.1"
    fake_tnc = reserve()
    fake_tnc.listen()
    api_reserve, agw_reserve = reserve(), reserve()
    api_port = api_reserve.getsockname()[1]
    agw_port = agw_reserve.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="pocketlink-tncd-smoke-") as tmp:
        config = Path(tmp) / "tncd.ini"
        config.write_text(f"""[server]
listen_host = 127.0.0.1
listen_port = {agw_port}
callsign = N0CALL
[client.0]
type = tcp
host = 127.0.0.1
port = {fake_tnc.getsockname()[1]}
reconnect = false
[api]
enabled = true
listen_host = 127.0.0.1
listen_port = {api_port}
""")
        api_reserve.close()
        agw_reserve.close()
        process = subprocess.Popen([executable, "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for attempt in range(50):
                try:
                    with urlopen(f"http://127.0.0.1:{api_port}/api/status", timeout=1) as response:
                        status = json.load(response)
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(f"tncd exited early: {process.returncode}")
                    time.sleep(0.1)
            else:
                raise RuntimeError("Status API did not start")
            assert status.get("version") == "2.1.0-pocketlink.1", status
            print("CLI and status API version verified:", status["version"])
            print("Only a local fake TCP KISS endpoint was used; no Bluetooth or RF.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            fake_tnc.close()


if __name__ == "__main__":
    main()
