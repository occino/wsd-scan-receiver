from pathlib import Path

from wsd_scan_receiver.config import Config
from wsd_scan_receiver.epsonscan2_bridge import EpsonScan2Bridge


def _config(tmp_path: Path) -> Config:
    return Config(
        device_name="Test Scanner",
        endpoint_uuid="uuid:test",
        http_port=5357,
        output_dir=tmp_path / "consume",
        debug=False,
        raw_dump_dir=tmp_path / "dumps",
        log_level="INFO",
        host_ip="192.0.2.20",
        interface=None,
        epson_printer_ip="192.0.2.21",
        epsonscan2_enabled=True,
        epsonscan2_helper=str(tmp_path / "helper"),
        epsonscan2_lib_path=None,
        epsonscan2_lib_dir=str(tmp_path / "lib"),
        epsonscan2_keepalive=True,
        epsonscan2_refresh_seconds=0,
        epson_debug_enabled=False,
        wsd_subscribe_enabled=False,
        wsd_subscribe_interval_seconds=60,
        uuid_file=tmp_path / "uuid",
    )


def test_find_library_from_configured_dir(tmp_path: Path) -> None:
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    expected = lib_dir / "libes2command.so"
    expected.write_bytes(b"")

    bridge = EpsonScan2Bridge(_config(tmp_path))

    assert bridge._find_library() == expected


def test_prepend_env_path() -> None:
    assert EpsonScan2Bridge._prepend_env_path(None, "/epson") == "/epson"
    assert EpsonScan2Bridge._prepend_env_path("/usr/lib", "/epson") == "/epson:/usr/lib"


def test_refresh_seconds_is_passed_to_helper(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    config = Config(
        **{
            **config.__dict__,
            "epsonscan2_refresh_seconds": 20,
            "epsonscan2_helper": str(tmp_path / "helper"),
        }
    )
    (tmp_path / "helper").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "lib").mkdir(exist_ok=True)
    (tmp_path / "lib" / "libes2command.so").write_bytes(b"")
    captured: dict[str, list[str]] = {}

    def fake_run_loop(command, *_args):
        captured["command"] = command

    bridge = EpsonScan2Bridge(config)
    monkeypatch.setattr(bridge, "_run_loop", fake_run_loop)

    bridge.start()
    assert bridge._thread is not None
    bridge._thread.join(timeout=2)

    assert captured["command"][-2:] == ["--refresh-seconds", "20"]
