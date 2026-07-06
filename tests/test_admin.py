import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from wsd_scan_receiver.admin import make_admin_handler
from wsd_scan_receiver.config import (
    PostProcessingSettingsStore,
    ScanTicketStore,
    ServiceSettingsStore,
)


def _server(
    service_store: ServiceSettingsStore,
    post_processing_store: PostProcessingSettingsStore,
    scan_store: ScanTicketStore,
) -> tuple[ThreadingHTTPServer, Thread, int]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_admin_handler(service_store, post_processing_store, scan_store),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def test_admin_get_scan_config_returns_defaults(tmp_path: Path) -> None:
    service_store = ServiceSettingsStore(tmp_path / "config.json")
    post_processing_store = PostProcessingSettingsStore(tmp_path / "config.json")
    scan_store = ScanTicketStore(tmp_path / "config.json")
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/scan-config")
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    data = json.loads(body)
    assert data["format"] == "exif"
    assert data["resolution"] == 100


def test_admin_post_scan_config_writes_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/scan-config",
            body=json.dumps({"resolution": 300, "compression_quality": 75}),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    data = json.loads(body)
    assert data["resolution"] == 300
    assert data["compression_quality"] == 75
    assert json.loads(config_file.read_text(encoding="utf-8"))["scan"]["resolution"] == 300
    assert scan_store.get().resolution == 300


def test_admin_post_invalid_value_keeps_current_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    before = scan_store.get()
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/scan-config",
            body=json.dumps({"resolution": 0}),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 400
    assert "resolution" in json.loads(body)["error"]
    assert scan_store.get() == before
    assert not config_file.exists()


def test_admin_get_combined_config_returns_service_and_scan(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/config")
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    data = json.loads(body)
    assert data["service"]["WSD_DEVICE_NAME"] == "Paperless WSD Scanner"
    assert data["service"]["LOG_LEVEL"] == "INFO"
    assert data["post_processing"]["enabled"] is True
    assert data["post_processing"]["crop_mode"] == "auto"
    assert data["post_processing"]["background_threshold"] == 220
    assert data["post_processing"]["crop_side_padding"] == 0
    assert data["post_processing"]["crop_bottom_padding"] == 0
    assert data["scan"]["format"] == "exif"


def test_admin_post_combined_config_writes_service_and_scan(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/config",
            body=json.dumps(
                {
                    "service": {
                        "WSD_DEVICE_NAME": "Office Scanner",
                        "WSD_HOST": "192.0.2.10",
                        "WSD_INTERFACE": "ens16",
                        "WSD_SCANNER_IP": "192.0.2.21",
                        "DEBUG": True,
                        "LOG_LEVEL": "debug",
                    },
                    "post_processing": {
                        "enabled": False,
                        "crop_mode": "DIN-A4",
                        "background_threshold": 210,
                        "document_contrast": 20,
                        "min_document_width_percent": 70,
                        "min_document_height_percent": 80,
                        "crop_side_padding": 12,
                        "crop_bottom_padding": 18,
                    },
                    "scan": {"resolution": 300},
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    data = json.loads(body)
    assert data["service"]["WSD_DEVICE_NAME"] == "Office Scanner"
    assert data["service"]["LOG_LEVEL"] == "DEBUG"
    assert data["post_processing"]["enabled"] is True
    assert data["post_processing"]["crop_mode"] == "DIN-A4"
    assert data["post_processing"]["background_threshold"] == 210
    assert data["post_processing"]["crop_side_padding"] == 12
    assert data["post_processing"]["crop_bottom_padding"] == 18
    assert data["scan"]["resolution"] == 300
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["service"]["WSD_SCANNER_IP"] == "192.0.2.21"
    assert saved["post_processing"]["enabled"] is True
    assert saved["post_processing"]["crop_mode"] == "DIN-A4"
    assert saved["scan"]["resolution"] == 300


def test_admin_index_uses_english_tables_and_save_button(tmp_path: Path) -> None:
    service_store = ServiceSettingsStore(tmp_path / "config.json")
    post_processing_store = PostProcessingSettingsStore(tmp_path / "config.json")
    scan_store = ScanTicketStore(tmp_path / "config.json")
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    assert "<caption>Service parameters</caption>" in body
    assert "<caption>Document Cropping</caption>" in body
    assert "<caption>Scan parameters</caption>" in body
    assert 'type="checkbox"' not in body
    assert 'value="none"' in body
    assert 'value="auto"' in body
    assert 'value="DIN-A4"' in body
    assert body.count('class="section-card"') == 3
    assert "service-section" not in body
    assert "crop-section" not in body
    assert "scan-section" not in body
    assert body.index("<caption>Scan parameters</caption>") < body.index(
        "<caption>Document Cropping</caption>"
    )
    assert 'id="background_threshold"' in body
    assert (
        'data-tooltip="Corner brightness at or above this value disables auto-cropping. '
        'Range: 0-255."'
        in body
    )
    assert 'data-tooltip="Name advertised to scanners. Value: non-empty text."' in body
    assert (
        'data-tooltip="Requested output format. Values: exif, tiff-single-uncompressed."'
        in body
    )
    assert "syncPostProcessingControls" in body
    assert ">Save<" in body
    assert "Speichern" not in body
