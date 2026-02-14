from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from django.db import close_old_connections

from .arduino import ArduinoResult, _try_import_pyserial
from .models import Experiment, Frame


@dataclass(frozen=True)
class TelemetrySample:
    t_s: float
    rpm: float
    pressure_pa: float
    temperature_c: float
    mosfet: int


_DATA_RE = re.compile(
    r"^OK\s+DATA\s+(?P<t_ms>\d+)\s+(?P<rpm>\d+)\s+(?P<p>\d+(?:\.\d+)?)\s+(?P<t>-?\d+(?:\.\d+)?)\s+(?P<m>\d+)\s*$",
    re.IGNORECASE,
)


def parse_read_all(line: str) -> TelemetrySample | None:
    m = _DATA_RE.match(line.strip())
    if not m:
        return None
    try:
        t_s = float(m.group("t_ms")) / 1000.0
        rpm = float(m.group("rpm"))
        p = float(m.group("p"))
        t = float(m.group("t"))
        mosfet = int(m.group("m"))
    except ValueError:
        return None
    return TelemetrySample(t_s=t_s, rpm=rpm, pressure_pa=p, temperature_c=t, mosfet=mosfet)


class ArduinoSession:
    """
    Single persistent serial connection. Required for fast polling:
    re-opening the port usually resets Arduino and makes frequent polling impossible.
    """

    def __init__(self, *, port: str, baud_rate: int, boot_delay_s: float = 2.2):
        serial = _try_import_pyserial()
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial).")

        self._serial_mod = serial
        self._port = port
        self._baud = int(baud_rate)
        self._boot_delay_s = float(boot_delay_s)
        self._lock = threading.Lock()
        self._ser = None

    def _ensure_open(self):
        if self._ser is not None:
            return
        ser = self._serial_mod.Serial(port=self._port, baudrate=self._baud, timeout=0.25)
        # Arduino often resets on open.
        if self._boot_delay_s > 0:
            time.sleep(self._boot_delay_s)
        ser.reset_input_buffer()
        self._ser = ser

    def close(self):
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                finally:
                    self._ser = None

    def request_one_line(self, *, command: str, timeout_s: float = 1.0) -> ArduinoResult:
        """
        Send a command and wait for a single OK/ERR line.
        """
        with self._lock:
            try:
                self._ensure_open()
                assert self._ser is not None
                self._ser.write((command.strip() + "\n").encode("utf-8", errors="replace"))
                self._ser.flush()
                t0 = time.monotonic()
                lines: list[str] = []
                while time.monotonic() - t0 < timeout_s:
                    raw = self._ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    lines.append(line)
                    up = line.upper()
                    if up.startswith("ERR"):
                        return ArduinoResult(ok=False, confirmed=False, response_lines=lines, error=line)
                    if up.startswith("OK"):
                        return ArduinoResult(ok=True, confirmed=True, response_lines=lines)
                return ArduinoResult(ok=False, confirmed=False, response_lines=lines, error="Timeout waiting for response.")
            except Exception as exc:
                return ArduinoResult(ok=False, confirmed=False, response_lines=[], error=str(exc))


_session_lock = threading.Lock()
_sessions: dict[tuple[str, int], ArduinoSession] = {}


def get_session(*, port: str, baud_rate: int) -> ArduinoSession:
    key = (port, int(baud_rate))
    with _session_lock:
        sess = _sessions.get(key)
        if sess is not None:
            return sess
        sess = ArduinoSession(port=port, baud_rate=baud_rate)
        _sessions[key] = sess
        return sess


class ExperimentPoller:
    def __init__(self, *, experiment_id: int, port: str, baud_rate: int, poll_hz: float = 20.0, batch_size: int = 20):
        self.experiment_id = int(experiment_id)
        self.port = port
        self.baud_rate = int(baud_rate)
        self.poll_hz = float(poll_hz)
        self.batch_size = int(batch_size)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"poller-exp-{self.experiment_id}", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0):
        self._stop.set()
        self._thread.join(timeout=join_timeout_s)

    def _run(self):
        period_s = 1.0 / max(1.0, self.poll_hz)
        next_t = time.monotonic()
        buf: list[Frame] = []

        while not self._stop.is_set():
            close_old_connections()

            try:
                exp = Experiment.objects.get(pk=self.experiment_id)
            except Experiment.DoesNotExist:
                break

            if (exp.status or "").lower() != "running":
                time.sleep(0.25)
                continue

            sess = get_session(port=self.port, baud_rate=self.baud_rate)
            res = sess.request_one_line(command="READ_ALL", timeout_s=0.8)
            if res.ok and res.confirmed and res.response_lines:
                sample = parse_read_all(res.response_lines[-1])
                if sample is not None:
                    buf.append(
                        Frame(
                            experiment=exp,
                            second=sample.t_s,
                            temperature=sample.temperature_c,
                            dif_pressure=sample.pressure_pa,
                        )
                    )

            if len(buf) >= self.batch_size:
                try:
                    Frame.objects.bulk_create(buf, batch_size=self.batch_size)
                    buf = []
                except Exception:
                    # If DB temporarily fails, keep buffer small and continue.
                    buf = buf[-10:]

            next_t += period_s
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_t = time.monotonic()

        if buf:
            try:
                close_old_connections()
                Frame.objects.bulk_create(buf, batch_size=min(len(buf), self.batch_size))
            except Exception:
                pass


_pollers_lock = threading.Lock()
_pollers: dict[int, ExperimentPoller] = {}


def ensure_poller_running(experiment: Experiment, *, poll_hz: float = 20.0, batch_size: int = 20) -> None:
    if not experiment.serial_port:
        return
    if (experiment.status or "").lower() != "running":
        return

    with _pollers_lock:
        if experiment.id in _pollers:
            return
        poller = ExperimentPoller(
            experiment_id=experiment.id,
            port=experiment.serial_port,
            baud_rate=experiment.baud_rate,
            poll_hz=poll_hz,
            batch_size=batch_size,
        )
        _pollers[experiment.id] = poller
        poller.start()


def stop_poller(experiment_id: int) -> None:
    with _pollers_lock:
        poller = _pollers.pop(int(experiment_id), None)
    if poller is not None:
        poller.stop()
