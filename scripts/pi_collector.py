#!/usr/bin/env python3
"""
Raspberry Pi telemetry collector:
- Opens Arduino serial once (important for high-frequency polling).
- Polls Arduino using READ_ALL.
- Sends frames to Django endpoint /api/experiments/<id>/frames/batch/ in batches.

Environment variables:
- EXPERIMENT_ID (required)
- SERVER_BASE_URL (default: http://localhost:8000)
- SERIAL_PORT (optional; if empty, will try to read from /api/.../summary/)
- BAUD_RATE (optional; default: 115200)
- POLL_HZ (optional; default: 20)
- BATCH_SIZE (optional; default: 20)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


def _env_int(name: str, default: int) -> int:
    val = (os.getenv(name) or "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = (os.getenv(name) or "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _http_json(method: str, url: str, payload: dict | None = None, timeout_s: float = 5.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _parse_read_all(line: str) -> dict | None:
    # Expected: "OK DATA <t_ms> <rpm> <pressure_kpa> <temp_c> <mosfet>"
    parts = line.strip().split()
    if len(parts) < 7:
        return None
    if parts[0].upper() != "OK" or parts[1].upper() != "DATA":
        return None

    try:
        t_ms = float(parts[2])
        rpm = float(parts[3])
        pressure_kpa = float(parts[4])
        temp_c = float(parts[5])
        mosfet = int(parts[6])
    except ValueError:
        return None

    return {
        "t_s": t_ms / 1000.0,
        "rpm": rpm,
        "pressure_kpa": pressure_kpa,
        "temp_c": temp_c,
        "mosfet": mosfet,
    }


def main() -> int:
    experiment_id = (os.getenv("EXPERIMENT_ID") or "").strip()
    if not experiment_id:
        raise SystemExit("EXPERIMENT_ID is required")

    server_base = (os.getenv("SERVER_BASE_URL") or "http://localhost:8000").strip().rstrip("/")
    baud_rate = _env_int("BAUD_RATE", 115200)
    poll_hz = max(1.0, _env_float("POLL_HZ", 20.0))
    batch_size = max(1, _env_int("BATCH_SIZE", 20))

    summary_url = f"{server_base}/api/experiments/{experiment_id}/summary/"
    ingest_url = f"{server_base}/api/experiments/{experiment_id}/frames/batch/"

    serial_port = (os.getenv("SERIAL_PORT") or "").strip()
    if not serial_port:
        # Pull port/baud from DB via summary API to reduce manual config.
        try:
            summary = _http_json("GET", summary_url, None, timeout_s=5.0)
            exp = (summary or {}).get("experiment") or {}
            serial_port = (exp.get("serial_port") or "").strip()
            baud_rate = int(exp.get("baud_rate") or baud_rate)
        except Exception:
            # We'll fail later with a clear error if still empty.
            pass

    if not serial_port:
        raise SystemExit("SERIAL_PORT is empty (set it in env or in the Experiment in UI).")

    try:
        import serial  # type: ignore
    except Exception:
        raise SystemExit("pyserial is required (pip install pyserial).")

    frames: list[dict] = []
    last_status_check = 0.0
    running = False

    # Keep one serial connection open for max polling rate.
    with serial.Serial(port=serial_port, baudrate=baud_rate, timeout=0.2) as ser:
        # Arduino typically resets on serial open; give it time.
        time.sleep(2.0)
        ser.reset_input_buffer()

        # Basic handshake (optional)
        ser.write(b"PING\n")
        ser.flush()
        _ = ser.readline()

        period_s = 1.0 / poll_hz
        next_t = time.monotonic()

        while True:
            now = time.monotonic()

            # Periodically check experiment status; only log when RUNNING.
            if now - last_status_check > 1.0:
                last_status_check = now
                try:
                    summary = _http_json("GET", summary_url, None, timeout_s=3.0)
                    status = ((summary or {}).get("experiment") or {}).get("status") or ""
                    running = str(status).strip().lower() == "running"
                except Exception:
                    # If server is down temporarily, keep last known state and retry later.
                    pass

            if not running:
                time.sleep(0.25)
                continue

            # Poll Arduino
            ser.write(b"READ_ALL\n")
            ser.flush()
            line = ser.readline().decode("utf-8", errors="replace").strip()
            parsed = _parse_read_all(line)
            if parsed is not None:
                frames.append(
                    {
                        "second": parsed["t_s"],
                        "temperature": parsed["temp_c"],
                        "dif_pressure": parsed["pressure_kpa"],
                    }
                )

            # Send batch
            if len(frames) >= batch_size:
                payload = {"frames": frames}
                try:
                    resp = _http_json("POST", ingest_url, payload, timeout_s=5.0)
                    if (resp or {}).get("status") == "ok":
                        frames = []
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    # Keep frames; retry on next iteration.
                    pass

            # Rate limit
            next_t += period_s
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # We're lagging; resync to avoid runaway backlog.
                next_t = time.monotonic()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
