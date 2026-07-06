from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread

from tests.helpers import default_scan_ticket
from wsd_scan_receiver.config import Config
from wsd_scan_receiver.receiver import make_handler, read_chunked_body
from wsd_scan_receiver.soap import SOAP12, WSA, WST


def _config(tmp_path: Path, port: int = 0) -> Config:
    return Config(
        device_name="Test Scanner",
        endpoint_uuid="uuid:test",
        http_port=port,
        output_dir=tmp_path / "scans",
        debug=True,
        raw_dump_dir=tmp_path / "dumps",
        log_level="INFO",
        host_ip="127.0.0.1",
        interface=None,
        scanner_ip=None,
        max_post_bytes=100 * 1024 * 1024,
        wsd_subscribe_enabled=False,
        wsd_subscribe_interval_seconds=60,
        scan_ticket=default_scan_ticket(),
        uuid_file=tmp_path / "uuid",
    )


def test_http_post_soap_get(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_config(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:wst="{WST}">
  <s:Header>
    <a:Action>{WST}/Get</a:Action>
    <a:MessageID>uuid:message</a:MessageID>
  </s:Header>
  <s:Body><wst:Get /></s:Body>
</s:Envelope>""".encode()

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/scanner",
            body=payload,
            headers={"Content-Type": "application/soap+xml"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    assert b"Test Scanner" in body
    assert list((tmp_path / "dumps").glob("http-post-*.xml"))


def test_http_healthz(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_config(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz")
        response = conn.getresponse()
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 200
    assert body == b"ok\n"


def test_http_post_binary_payload_writes_scan(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_config(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/scanner",
            body=b"%PDF-1.7\n",
            headers={"Content-Type": "application/pdf"},
        )
        response = conn.getresponse()
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 202
    scans = list((tmp_path / "scans").glob("scan-*.pdf"))
    assert scans
    assert scans[0].read_bytes().startswith(b"%PDF")


def test_read_chunked_body() -> None:
    stream = BytesIO(b"4\r\n<s:E\r\n4\r\nvent\r\n0\r\n\r\n")

    assert read_chunked_body(stream) == b"<s:Event"


def test_read_chunked_body_enforces_max_bytes() -> None:
    stream = BytesIO(b"4\r\n<s:E\r\n4\r\nvent\r\n0\r\n\r\n")

    try:
        read_chunked_body(stream, max_bytes=4)
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_http_post_rejects_large_payload(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = Config(
        device_name=config.device_name,
        endpoint_uuid=config.endpoint_uuid,
        http_port=config.http_port,
        output_dir=config.output_dir,
        debug=config.debug,
        raw_dump_dir=config.raw_dump_dir,
        log_level=config.log_level,
        host_ip=config.host_ip,
        interface=config.interface,
        scanner_ip=config.scanner_ip,
        max_post_bytes=4,
        wsd_subscribe_enabled=config.wsd_subscribe_enabled,
        wsd_subscribe_interval_seconds=config.wsd_subscribe_interval_seconds,
        scan_ticket=config.scan_ticket,
        uuid_file=config.uuid_file,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/scanner",
            body=b"12345",
            headers={"Content-Type": "application/octet-stream"},
        )
        response = conn.getresponse()
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 413


def test_http_post_chunked_scan_available_event(tmp_path: Path) -> None:
    seen_events: list[bytes] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_config(tmp_path)))
    server.RequestHandlerClass = make_handler(
        _config(tmp_path),
        lambda payload: seen_events.append(payload),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Header>
    <a:Action>http://schemas.microsoft.com/windows/2006/08/wdp/scan/ScanAvailableEvent</a:Action>
    <a:MessageID>uuid:event</a:MessageID>
  </s:Header>
  <s:Body><wscn:ScanAvailableEvent /></s:Body>
</s:Envelope>""".encode()

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/events")
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.putheader("Content-Type", "application/soap+xml; charset=utf-8")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        halfway = len(payload) // 2
        for chunk in (payload[:halfway], payload[halfway:]):
            conn.send(f"{len(chunk):X}\r\n".encode())
            conn.send(chunk)
            conn.send(b"\r\n")
        conn.send(b"0\r\n\r\n")
        response = conn.getresponse()
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 202
    dumps = list((tmp_path / "dumps").glob("http-post-*.xml"))
    assert dumps
    assert b"ScanAvailableEvent" in dumps[0].read_bytes()
    assert len(seen_events) == 1
    assert b"ScanAvailableEvent" in seen_events[0]
