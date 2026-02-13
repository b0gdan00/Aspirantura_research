import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ArduinoResult:
    ok: bool
    confirmed: bool
    response_lines: list[str]
    error: str | None = None


def _try_import_pyserial():
    try:
        import serial  # type: ignore
    except Exception:
        return None
    return serial


def send_command_and_wait_ack(
    *,
    port: str,
    baud_rate: int,
    command: str,
    timeout_s: float = 2.0,
    write_line_ending: str = "\n",
) -> ArduinoResult:
    """
    Minimal, protocol-agnostic command/ack helper.

    Success heuristic (until protocol is specified):
    - any received line that contains "OK" => confirmed
    - any received line that contains "ERR" => failure
    """
    serial = _try_import_pyserial()
    if serial is None:
        return ArduinoResult(
            ok=False,
            confirmed=False,
            response_lines=[],
            error="pyserial is not installed (pip install pyserial).",
        )

    if not port:
        return ArduinoResult(ok=False, confirmed=False, response_lines=[], error="Serial port is empty.")

    start = time.monotonic()
    lines: list[str] = []

    try:
        with serial.Serial(port=port, baudrate=baud_rate, timeout=0.1) as ser:
            ser.reset_input_buffer()
            ser.write((command + write_line_ending).encode("utf-8", errors="replace"))
            ser.flush()

            while time.monotonic() - start < timeout_s:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                lines.append(line)

                upper = line.upper()
                if "ERR" in upper:
                    return ArduinoResult(ok=False, confirmed=False, response_lines=lines, error=line)
                if "OK" in upper:
                    return ArduinoResult(ok=True, confirmed=True, response_lines=lines)

            return ArduinoResult(ok=False, confirmed=False, response_lines=lines, error="Timeout waiting for ACK.")
    except Exception as exc:
        return ArduinoResult(ok=False, confirmed=False, response_lines=lines, error=str(exc))

