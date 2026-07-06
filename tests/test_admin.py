import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from wsd_scan_receiver.admin import MAX_ADMIN_POST_BYTES, make_admin_handler
from wsd_scan_receiver.config import (
    PostProcessingSettingsStore,
    ScanTicketStore,
    ServiceSettingsStore,
    UiSettingsStore,
)


def _server(
    service_store: ServiceSettingsStore,
    post_processing_store: PostProcessingSettingsStore,
    scan_store: ScanTicketStore,
    ui_store: UiSettingsStore | None = None,
) -> tuple[ThreadingHTTPServer, Thread, int]:
    ui_store = ui_store or UiSettingsStore(service_store.config_file)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_admin_handler(service_store, post_processing_store, scan_store, ui_store),
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
    assert data["resolution"] == 300
    assert data["compression_quality"] == 50


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


def test_admin_post_rejects_oversized_body(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/api/config")
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(MAX_ADMIN_POST_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 400
    assert b"request body must not exceed" in body
    assert not config_file.exists()


def test_admin_post_rejects_invalid_utf8_json(tmp_path: Path) -> None:
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
            body=b"\xff",
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 400
    assert b"request body must contain a JSON object" in body
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
    assert data["service"]["KEEP_ORIGINAL"] is False
    assert data["service"]["DEBUG"] is False
    assert data["service"]["LOG_LEVEL"] == "INFO"
    assert data["post_processing"]["enabled"] is True
    assert data["post_processing"]["crop_mode"] == "DIN-A4"
    assert data["post_processing"]["background_threshold"] == 220
    assert data["post_processing"]["crop_side_padding"] == 20
    assert data["post_processing"]["crop_bottom_padding"] == 20
    assert data["scan"]["format"] == "exif"
    assert data["ui"]["show_fixed_scan_parameters"] is False


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
                        "KEEP_ORIGINAL": False,
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
                    "ui": {"show_fixed_scan_parameters": True},
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
    assert data["service"]["KEEP_ORIGINAL"] is False
    assert data["service"]["LOG_LEVEL"] == "DEBUG"
    assert data["post_processing"]["enabled"] is True
    assert data["post_processing"]["crop_mode"] == "DIN-A4"
    assert data["post_processing"]["background_threshold"] == 210
    assert data["post_processing"]["crop_side_padding"] == 12
    assert data["post_processing"]["crop_bottom_padding"] == 18
    assert data["scan"]["resolution"] == 300
    assert data["ui"]["show_fixed_scan_parameters"] is True
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["service"]["WSD_SCANNER_IP"] == "192.0.2.21"
    assert saved["service"]["KEEP_ORIGINAL"] is False
    assert saved["post_processing"]["enabled"] is True
    assert saved["post_processing"]["crop_mode"] == "DIN-A4"
    assert saved["scan"]["resolution"] == 300
    assert saved["ui"]["show_fixed_scan_parameters"] is True


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
    assert "<label for=\"KEEP_ORIGINAL\">Keep original</label>" in body
    assert 'id="KEEP_ORIGINAL" name="KEEP_ORIGINAL" type="checkbox" value="true"' in body
    assert (
        'id="KEEP_ORIGINAL" name="KEEP_ORIGINAL" type="checkbox" value="true" checked'
        not in body
    )
    assert "<caption>Document Cropping</caption>" in body
    assert "<caption>Scan parameters</caption>" in body
    assert (
        '<label for="show_fixed_scan_parameters">Show parameters with fixed values</label>'
        in body
    )
    assert (
        'id="show_fixed_scan_parameters" name="show_fixed_scan_parameters" '
        'type="checkbox" value="true"'
        in body
    )
    assert (
        'id="resolution" name="resolution"><option value="100">100</option>'
        '<option value="300" selected>300</option>'
        in body
    )
    assert 'class="fixed-scan-parameter"' in body
    assert "syncFixedScanRows" in body
    assert "row.classList.toggle('hidden', !visible)" in body
    assert ".fixed-scan-parameter.hidden" in body
    assert (
        'id="compression_quality" name="compression_quality" disabled>'
        '<option value="50" selected>50</option>'
        in body
    )
    assert (
        'id="content_type" name="content_type" disabled>'
        '<option value="Text" selected>Text</option>'
        in body
    )
    assert 'select:disabled' in body
    assert 'cursor: not-allowed;' in body
    assert 'value="none"' in body
    assert 'value="auto"' in body
    assert 'value="DIN-A4"' in body
    assert 'class="auto-crop-parameter hidden"' in body
    assert ".auto-crop-parameter.hidden" in body
    assert "const autoCropRows" in body
    assert "row.classList.toggle('hidden', !autoEnabled)" in body
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
    assert 'id="default_values" type="application/json"' in body
    assert '"show_fixed_scan_parameters": false' in body
    assert '"compression_quality": 50' in body
    assert '"crop_mode": "DIN-A4"' in body
    assert (
        '<button id="restore_defaults_button" class="secondary-button" type="button">'
        in body
    )
    assert "Restore defaults" in body
    assert "restoreDefaultsButton.addEventListener('click', restoreDefaults)" in body
    assert "function restoreDefaults()" in body
    assert '<button id="save_button" type="submit">Save</button>' in body
    assert 'class="topbar"' in body
    assert "Saved." not in body
    assert ".saved" in body
    assert ".error" in body
    assert "setTemporaryButtonState('saved', 'Saved', true)" in body
    assert "setTemporaryButtonState('error', 'Error', false)" in body
    assert "Speichern" not in body


def test_admin_form_post_unchecked_keep_original_saves_false(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    server, thread, port = _server(service_store, post_processing_store, scan_store)

    body = "&".join(
        [
            "WSD_DEVICE_NAME=Paperless",
            "WSD_HOST=",
            "WSD_INTERFACE=ens16",
            "WSD_SCANNER_IP=192.0.2.21",
            "DEBUG=false",
            "LOG_LEVEL=INFO",
            "crop_mode=DIN-A4",
            "resolution=300",
            "compression_quality=50",
            "format=exif",
            "input_source=Auto",
            "content_type=Text",
            "color_processing=RGB24",
            "images_to_transfer=1",
            "width=8500",
            "height=11700",
            "region_x=0",
            "region_y=0",
            "region_width=8500",
            "region_height=11700",
            "brightness=0",
            "contrast=0",
            "sharpness=0",
            "rotation=0",
            "scaling_width=100",
            "scaling_height=100",
        ]
    )

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/config",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    assert service_store.get().keep_original is False


def test_admin_form_post_saves_show_fixed_scan_parameters(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    service_store = ServiceSettingsStore(config_file)
    post_processing_store = PostProcessingSettingsStore(config_file)
    scan_store = ScanTicketStore(config_file)
    ui_store = UiSettingsStore(config_file)
    server, thread, port = _server(
        service_store,
        post_processing_store,
        scan_store,
        ui_store,
    )

    body = "&".join(
        [
            "WSD_DEVICE_NAME=Paperless",
            "WSD_HOST=",
            "WSD_INTERFACE=ens16",
            "WSD_SCANNER_IP=192.0.2.21",
            "DEBUG=false",
            "LOG_LEVEL=INFO",
            "crop_mode=DIN-A4",
            "show_fixed_scan_parameters=true",
            "resolution=300",
            "compression_quality=50",
            "format=exif",
            "input_source=Auto",
            "content_type=Text",
            "color_processing=RGB24",
            "images_to_transfer=1",
            "width=8500",
            "height=11700",
            "region_x=0",
            "region_y=0",
            "region_width=8500",
            "region_height=11700",
            "brightness=0",
            "contrast=0",
            "sharpness=0",
            "rotation=0",
            "scaling_width=100",
            "scaling_height=100",
        ]
    )

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/config",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    assert ui_store.get().show_fixed_scan_parameters is True
    assert json.loads(config_file.read_text(encoding="utf-8"))["ui"] == {
        "show_fixed_scan_parameters": True
    }
