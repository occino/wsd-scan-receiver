"""Optional bridge to Epson Scan 2's native push-scan readiness API."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .config import Config

LOGGER = logging.getLogger(__name__)


class EpsonScan2Bridge:
    """Run the native Epson Scan 2 helper when Epson libraries are available."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.config.epsonscan2_enabled:
            return
        if not self.config.epson_printer_ip:
            LOGGER.warning("EpsonScan2 bridge enabled but EPSON_PRINTER_IP is not set")
            return

        helper = self._find_helper()
        if helper is None:
            LOGGER.warning(
                "EpsonScan2 bridge helper not found; push-scan ready state cannot be set",
                extra={"expected_helper": self.config.epsonscan2_helper},
            )
            return

        library = self._find_library()
        if library is None:
            LOGGER.warning(
                "EpsonScan2 library not found; mount libes2command.so and set "
                "EPSONSCAN2_LIB_PATH or EPSONSCAN2_LIB_DIR",
                extra={
                    "lib_path": self.config.epsonscan2_lib_path,
                    "lib_dir": self.config.epsonscan2_lib_dir,
                },
            )
            return

        env = os.environ.copy()
        if self.config.epsonscan2_lib_dir:
            env["LD_LIBRARY_PATH"] = self._prepend_env_path(
                env.get("LD_LIBRARY_PATH"),
                self.config.epsonscan2_lib_dir,
            )

        command = [
            str(helper),
            "--library",
            str(library),
            "--address",
            self.config.epson_printer_ip,
            "--name",
            self.config.device_name,
        ]
        if self.config.epsonscan2_keepalive:
            command.append("--keepalive")
        if self.config.epsonscan2_refresh_seconds > 0:
            command.extend(
                ["--refresh-seconds", str(self.config.epsonscan2_refresh_seconds)]
            )

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(command, env, str(helper), str(library)),
            name="epsonscan2-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run_loop(
        self,
        command: list[str],
        env: dict[str, str],
        helper: str,
        library: str,
    ) -> None:
        while not self._stop.is_set():
            LOGGER.info(
                "starting EpsonScan2 push-scan bridge",
                extra={
                    "helper": helper,
                    "library": library,
                    "printer_ip": self.config.epson_printer_ip,
                    "keepalive": self.config.epsonscan2_keepalive,
                    "refresh_seconds": self.config.epsonscan2_refresh_seconds,
                },
            )
            self._process = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._log_output()
            if self._stop.is_set() or not self.config.epsonscan2_keepalive:
                break
            LOGGER.info("restarting EpsonScan2 bridge after exit", extra={"delay_seconds": 5})
            self._stop.wait(5)

    def _log_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            LOGGER.info("EpsonScan2 bridge output", extra={"line": line.rstrip()})
        return_code = process.wait()
        LOGGER.info("EpsonScan2 bridge exited", extra={"return_code": return_code})

    def _find_helper(self) -> Path | None:
        configured = Path(self.config.epsonscan2_helper)
        if configured.exists():
            return configured
        found = shutil.which(self.config.epsonscan2_helper)
        return Path(found) if found else None

    def _find_library(self) -> Path | None:
        if self.config.epsonscan2_lib_path:
            configured = Path(self.config.epsonscan2_lib_path)
            if configured.exists():
                return configured
        if self.config.epsonscan2_lib_dir:
            candidate = Path(self.config.epsonscan2_lib_dir) / "libes2command.so"
            if candidate.exists():
                return candidate
        for candidate in (
            Path("/usr/lib/epsonscan2/libes2command.so"),
            Path("/usr/lib/x86_64-linux-gnu/epsonscan2/libes2command.so"),
            Path("/opt/epsonscan2/libes2command.so"),
        ):
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _prepend_env_path(existing: str | None, value: str) -> str:
        if not existing:
            return value
        return f"{value}:{existing}"
