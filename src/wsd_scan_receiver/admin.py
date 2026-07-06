"""Web UI for editing scan ticket parameters."""

from __future__ import annotations

import html
import json
import logging
import socket
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from .config import (
    LOG_LEVELS,
    POST_PROCESSING_FIELDS,
    SCAN_TICKET_ALLOWED_VALUES,
    SCAN_TICKET_POSITIVE_INT_FIELDS,
    SCAN_TICKET_TEXT_FIELDS,
    SERVICE_SETTINGS_FIELDS,
    PostProcessingSettingsStore,
    ScanTicketStore,
    ServiceSettingsStore,
    post_processing_settings_from_mapping,
    post_processing_settings_to_dict,
    scan_ticket_from_mapping,
    scan_ticket_to_dict,
    service_settings_from_mapping,
    service_settings_to_dict,
)
from .receiver import ThreadingHTTPServerNoFqdn, ThreadingHTTPServerV6

LOGGER = logging.getLogger(__name__)

ADMIN_PORT = 8888


SCAN_FIELD_LABELS = {
    "format": "Format",
    "input_source": "Input source",
    "content_type": "Content type",
    "color_processing": "Color processing",
    "resolution": "Resolution (DPI)",
    "compression_quality": "Compression quality",
    "images_to_transfer": "Images to transfer",
    "width": "Media width",
    "height": "Media height",
    "region_x": "Region X",
    "region_y": "Region Y",
    "region_width": "Region width",
    "region_height": "Region height",
    "brightness": "Brightness",
    "contrast": "Contrast",
    "sharpness": "Sharpness",
    "rotation": "Rotation",
    "scaling_width": "Scaling width (%)",
    "scaling_height": "Scaling height (%)",
}
SCAN_FIELD_HELP = {
    "format": "Requested output format. Values: exif, tiff-single-uncompressed.",
    "input_source": (
        "Scanner input source used when the event does not provide one. Values: Auto, Platen."
    ),
    "content_type": "Scanner image optimization hint. Values: Text, Photo, Mixed.",
    "color_processing": (
        "Color mode requested from the scanner. Values: RGB24, Grayscale8, BlackAndWhite1."
    ),
    "resolution": (
        "Horizontal and vertical scan resolution in DPI. Positive integer; common values: 100, 300."
    ),
    "compression_quality": "Compression quality for compressed formats. Range: 1-100.",
    "images_to_transfer": "Number of images requested for one job. Positive integer; usually 1.",
    "width": "Input media width in WSD units. Positive integer from scanner capabilities.",
    "height": "Input media height in WSD units. Positive integer from scanner capabilities.",
    "region_x": "Scan region X offset in WSD units. Integer; 0 starts at the left edge.",
    "region_y": "Scan region Y offset in WSD units. Integer; 0 starts at the top edge.",
    "region_width": "Scan region width in WSD units. Positive integer from scanner capabilities.",
    "region_height": "Scan region height in WSD units. Positive integer from scanner capabilities.",
    "brightness": "Scanner-dependent brightness adjustment. Integer; 0 is neutral.",
    "contrast": "Scanner-dependent contrast adjustment. Integer; 0 is neutral.",
    "sharpness": "Scanner-dependent sharpness adjustment. Integer; 0 is neutral.",
    "rotation": "Rotation requested from the scanner. Values: 0, 90, 180, 270.",
    "scaling_width": "Horizontal scaling percentage. Positive integer; 100 means no scaling.",
    "scaling_height": "Vertical scaling percentage. Positive integer; 100 means no scaling.",
}
SCAN_FIELD_ORDER = [
    "format",
    "input_source",
    "content_type",
    "color_processing",
    "resolution",
    "compression_quality",
    "images_to_transfer",
    "width",
    "height",
    "region_x",
    "region_y",
    "region_width",
    "region_height",
    "brightness",
    "contrast",
    "sharpness",
    "rotation",
    "scaling_width",
    "scaling_height",
]
SERVICE_FIELD_LABELS = {
    "WSD_DEVICE_NAME": "Device name",
    "WSD_HOST": "Advertised host",
    "WSD_INTERFACE": "Network interface",
    "WSD_SCANNER_IP": "Scanner IP",
    "DEBUG": "Debug logging",
    "LOG_LEVEL": "Log level",
}
SERVICE_FIELD_HELP = {
    "WSD_DEVICE_NAME": "Name advertised to scanners. Value: non-empty text.",
    "WSD_HOST": (
        "Host/IP advertised in WSD XAddrs. Value: empty for auto-detection or an IP/hostname."
    ),
    "WSD_INTERFACE": (
        "Network interface used for IPv6 WS-Discovery. "
        "Value: empty or an interface name such as ens16."
    ),
    "WSD_SCANNER_IP": "Scanner IP for directed WSD probing. Value: empty or an IPv4/IPv6 address.",
    "DEBUG": "Enable verbose logging and raw debug dumps. Values: false, true.",
    "LOG_LEVEL": "Python log verbosity. Values: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET.",
}
SERVICE_FIELD_ORDER = [
    "WSD_DEVICE_NAME",
    "WSD_HOST",
    "WSD_INTERFACE",
    "WSD_SCANNER_IP",
    "DEBUG",
    "LOG_LEVEL",
]
POST_PROCESSING_FIELD_LABELS = {
    "crop_mode": "Crop mode",
    "background_threshold": "Background threshold",
    "document_contrast": "Document contrast",
    "min_document_width_percent": "Minimum document width (%)",
    "min_document_height_percent": "Minimum document height (%)",
    "crop_side_padding": "Side crop padding (px)",
    "crop_bottom_padding": "Bottom crop padding (px)",
}
POST_PROCESSING_FIELD_HELP = {
    "crop_mode": (
        "Select none to skip cropping, auto for parameter-based document detection, "
        "or DIN-A4 for a fixed top-left A4 crop. Values: none, auto, DIN-A4."
    ),
    "background_threshold": (
        "Corner brightness at or above this value disables auto-cropping. Range: 0-255."
    ),
    "document_contrast": (
        "Brightness difference from detected background needed to identify paper. Range: 0-255."
    ),
    "min_document_width_percent": (
        "Ignore crop boxes narrower than this share of the full image. Range: 1-100."
    ),
    "min_document_height_percent": (
        "Ignore crop boxes shorter than this share of the full image. Range: 1-100."
    ),
    "crop_side_padding": (
        "Pixels added back on the detected free side. For top-left aligned documents, "
        "this is the right edge. Range: 0-500."
    ),
    "crop_bottom_padding": "Pixels added back below the detected document. Range: 0-500.",
}
POST_PROCESSING_FIELD_ORDER = [
    "crop_mode",
    "background_threshold",
    "document_contrast",
    "min_document_width_percent",
    "min_document_height_percent",
    "crop_side_padding",
    "crop_bottom_padding",
]


def _row(label: str, name: str, control: str, help_text: str) -> str:
    escaped_name = html.escape(name)
    escaped_label = html.escape(label)
    escaped_help = html.escape(help_text, quote=True)
    return (
        f'<tr><th><label for="{escaped_name}">{escaped_label}</label>'
        f'<span class="help" tabindex="0" data-tooltip="{escaped_help}" '
        f'aria-label="{escaped_help}">?</span></th>'
        f"<td>{control}</td></tr>"
    )


def _select_for(name: str, value: str | int | bool, options: list[str]) -> str:
    escaped_name = html.escape(name)
    option_html = []
    selected_value = str(value).lower() if isinstance(value, bool) else str(value)
    for option in options:
        selected = " selected" if str(option) == selected_value else ""
        escaped_option = html.escape(str(option))
        option_html.append(f'<option value="{escaped_option}"{selected}>{escaped_option}</option>')
    return f'<select id="{escaped_name}" name="{escaped_name}">{"".join(option_html)}</select>'


def _scan_input_for(name: str, value: str | int) -> str:
    label = SCAN_FIELD_LABELS[name]
    escaped_name = html.escape(name)
    if name in SCAN_TICKET_TEXT_FIELDS or name in SCAN_TICKET_ALLOWED_VALUES:
        allowed = SCAN_TICKET_ALLOWED_VALUES.get(name)
        if allowed is not None:
            control = _select_for(name, value, [str(option) for option in sorted(allowed, key=str)])
        else:
            escaped_value = html.escape(str(value), quote=True)
            control = (
                f'<input id="{escaped_name}" name="{escaped_name}" '
                f'type="text" value="{escaped_value}">'
            )
    else:
        escaped_value = html.escape(str(value), quote=True)
        minimum = " min=\"1\"" if name in SCAN_TICKET_POSITIVE_INT_FIELDS else ""
        control = (
            f'<input id="{escaped_name}" name="{escaped_name}" '
            f'type="number"{minimum} step="1" value="{escaped_value}">'
        )
    return _row(label, name, control, SCAN_FIELD_HELP[name])


def _service_input_for(name: str, value: str | bool) -> str:
    label = SERVICE_FIELD_LABELS[name]
    escaped_name = html.escape(name)
    if name == "DEBUG":
        control = _select_for(name, value, ["false", "true"])
    elif name == "LOG_LEVEL":
        control = _select_for(name, str(value), sorted(LOG_LEVELS))
    else:
        escaped_value = html.escape(str(value), quote=True)
        control = (
            f'<input id="{escaped_name}" name="{escaped_name}" '
            f'type="text" value="{escaped_value}">'
        )
    return _row(label, name, control, SERVICE_FIELD_HELP[name])


def _number_input_for(
    name: str,
    value: int,
    *,
    minimum: int,
    maximum: int,
    extra: str = "",
) -> str:
    escaped_name = html.escape(name)
    escaped_value = html.escape(str(value), quote=True)
    return (
        f'<input id="{escaped_name}" name="{escaped_name}" type="number" '
        f'min="{minimum}" max="{maximum}" step="1" value="{escaped_value}"{extra}>'
    )


def _post_processing_input_for(
    name: str,
    value: bool | int | str,
    *,
    crop_mode: str,
) -> str:
    label = POST_PROCESSING_FIELD_LABELS[name]
    if name == "crop_mode":
        control = _select_for(name, str(value), ["none", "auto", "DIN-A4"])
        return _row(label, name, control, POST_PROCESSING_FIELD_HELP[name])
    disabled = "" if crop_mode == "auto" else " disabled"
    if name in {"background_threshold", "document_contrast"}:
        control = _number_input_for(name, int(value), minimum=0, maximum=255, extra=disabled)
    elif name in {"min_document_width_percent", "min_document_height_percent"}:
        control = _number_input_for(name, int(value), minimum=1, maximum=100, extra=disabled)
    else:
        control = _number_input_for(name, int(value), minimum=0, maximum=500, extra=disabled)
    return _row(label, name, control, POST_PROCESSING_FIELD_HELP[name])


def _section_card(caption: str, rows: str) -> str:
    escaped_caption = html.escape(caption)
    return f"""<section class="section-card">
        <table>
          <caption>{escaped_caption}</caption>
          <tbody>
          {rows}
          </tbody>
        </table>
      </section>"""


def render_index(
    service_values: dict[str, str | bool],
    post_processing_values: dict[str, bool | int | str],
    scan_values: dict[str, str | int],
    *,
    message: str = "",
) -> bytes:
    service_fields = "\n".join(
        _service_input_for(name, service_values[name]) for name in SERVICE_FIELD_ORDER
    )
    post_processing_fields = "\n".join(
        _post_processing_input_for(
            name,
            post_processing_values[name],
            crop_mode=str(post_processing_values["crop_mode"]),
        )
        for name in POST_PROCESSING_FIELD_ORDER
    )
    scan_fields = "\n".join(_scan_input_for(name, scan_values[name]) for name in SCAN_FIELD_ORDER)
    message_html = f'<span class="message">{html.escape(message)}</span>' if message else ""
    service_section = _section_card("Service parameters", service_fields)
    scan_section = _section_card("Scan parameters", scan_fields)
    post_processing_section = _section_card("Document Cropping", post_processing_fields)
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WSD Scan Receiver</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --fg: #20242a;
      --muted: #5c6675;
      --panel: #ffffff;
      --border: #ccd2db;
      --accent: #176b87;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #16191d;
        --fg: #f3f6f8;
        --muted: #aab3bf;
        --panel: #20252b;
        --border: #3a424c;
        --accent: #59b7d3;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(980px, calc(100% - 32px));
      margin: 28px auto;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    form {{
      display: grid;
      gap: 18px;
    }}
    .section-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 0;
    }}
    caption {{
      color: var(--fg);
      font-size: 17px;
      font-weight: 650;
      padding: 0 0 8px;
      text-align: left;
    }}
    th, td {{
      border-bottom: 1px solid var(--border);
      padding: 9px 0;
      vertical-align: middle;
    }}
    tr:last-child th, tr:last-child td {{
      border-bottom: 0;
    }}
    th {{
      width: 34%;
      color: var(--muted);
      font-weight: 550;
      padding-right: 18px;
      text-align: left;
      white-space: nowrap;
    }}
    .help {{
      display: inline-grid;
      place-items: center;
      position: relative;
      width: 18px;
      height: 18px;
      margin-left: 7px;
      border: 1px solid var(--border);
      border-radius: 50%;
      color: var(--muted);
      cursor: help;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      vertical-align: middle;
    }}
    .help::after {{
      position: absolute;
      z-index: 10;
      left: 26px;
      top: 50%;
      width: min(340px, calc(100vw - 80px));
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
      color: var(--fg);
      content: attr(data-tooltip);
      font-size: 13px;
      font-weight: 450;
      line-height: 1.35;
      opacity: 0;
      pointer-events: none;
      text-align: left;
      transform: translateY(-50%);
      transition: opacity 120ms ease;
      white-space: normal;
    }}
    .help:hover::after,
    .help:focus::after {{
      opacity: 1;
    }}
    .help:focus {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    td {{
      width: 66%;
    }}
    input, select {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--fg);
      font: inherit;
      padding: 7px 9px;
    }}
    .actions {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 14px;
      margin-top: 0;
    }}
    button {{
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 650;
      padding: 8px 16px;
      cursor: pointer;
    }}
    .message {{
      color: var(--accent);
      margin-right: auto;
    }}
    @media (max-width: 640px) {{
      th, td {{
        display: block;
        width: 100%;
        padding: 6px 0;
      }}
      th {{
        padding-right: 0;
        white-space: normal;
      }}
      td {{
        padding-bottom: 12px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>WSD Scan Receiver</h1>
    <form method="post" action="/api/config">
      {service_section}
      {scan_section}
      {post_processing_section}
      <div class="actions">{message_html}<button type="submit">Save</button></div>
    </form>
    <script>
      const cropMode = document.querySelector('#crop_mode');
      const autoControls = Array.from(document.querySelectorAll(
        '#background_threshold, #document_contrast, #min_document_width_percent, '
        + '#min_document_height_percent, #crop_side_padding, #crop_bottom_padding'
      ));
      function syncPostProcessingControls() {{
        const autoEnabled = cropMode.value === 'auto';
        for (const control of autoControls) {{
          control.disabled = !autoEnabled;
        }}
      }}
      cropMode.addEventListener('change', syncPostProcessingControls);
      syncPostProcessingControls();
    </script>
  </main>
</body>
</html>
"""
    return body.encode("utf-8")


class AdminRequestHandler(BaseHTTPRequestHandler):
    """Request handler bound to a ScanTicketStore by make_admin_handler."""

    server_version = "WsdScanAdmin/0.1"
    service_settings_store: ServiceSettingsStore
    post_processing_store: PostProcessingSettingsStore
    scan_ticket_store: ScanTicketStore

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info(
            "admin http request",
            extra={"peer": self.client_address[0], "line": fmt % args},
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(
                HTTPStatus.OK,
                render_index(
                    self.service_settings_store.as_dict(),
                    self.post_processing_store.as_dict(),
                    self.scan_ticket_store.as_dict(),
                ),
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/api/config":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": self.service_settings_store.as_dict(),
                    "post_processing": self.post_processing_store.as_dict(),
                    "scan": self.scan_ticket_store.as_dict(),
                },
            )
            return
        if self.path == "/api/scan-config":
            self._send_json(HTTPStatus.OK, self.scan_ticket_store.as_dict())
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/config", "/api/scan-config"}:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
            return

        try:
            values = self._read_values()
            if self.path == "/api/scan-config":
                scan_values = values
                service_settings = self.service_settings_store.get()
                post_processing = self.post_processing_store.get()
            else:
                service_values, post_processing_values, scan_values = self._split_values(values)
                service_settings_from_mapping(
                    service_values,
                    base=self.service_settings_store.get(),
                )
                post_processing_settings_from_mapping(
                    post_processing_values,
                    base=self.post_processing_store.get(),
                )
                scan_ticket_from_mapping(scan_values, base=self.scan_ticket_store.get())
                service_settings = self.service_settings_store.update(service_values)
                post_processing = self.post_processing_store.update(post_processing_values)
            scan_ticket = self.scan_ticket_store.update(scan_values)
        except ValueError as exc:
            if self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            else:
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    render_index(
                        self.service_settings_store.as_dict(),
                        self.post_processing_store.as_dict(),
                        self.scan_ticket_store.as_dict(),
                        message=str(exc),
                    ),
                    "text/html; charset=utf-8",
                )
            return

        if self.headers.get("Content-Type", "").lower().startswith("application/json"):
            if self.path == "/api/scan-config":
                self._send_json(HTTPStatus.OK, scan_ticket_to_dict(scan_ticket))
            else:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "service": service_settings_to_dict(service_settings),
                        "post_processing": post_processing_settings_to_dict(post_processing),
                        "scan": scan_ticket_to_dict(scan_ticket),
                    },
                )
            return
        self._send(
            HTTPStatus.OK,
            render_index(
                service_settings_to_dict(service_settings),
                post_processing_settings_to_dict(post_processing),
                scan_ticket_to_dict(scan_ticket),
                message="Saved.",
            ),
            "text/html; charset=utf-8",
        )

    def _split_values(
        self,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if "service" in values or "post_processing" in values or "scan" in values:
            service_values = values.get("service", {})
            post_processing_values = values.get("post_processing", {})
            scan_values = values.get("scan", {})
            if (
                not isinstance(service_values, dict)
                or not isinstance(post_processing_values, dict)
                or not isinstance(scan_values, dict)
            ):
                raise ValueError("service, post_processing, and scan must contain JSON objects")
            return service_values, post_processing_values, scan_values
        service_values = {name: values[name] for name in SERVICE_SETTINGS_FIELDS if name in values}
        post_processing_values = {
            name: values[name] for name in POST_PROCESSING_FIELDS if name in values
        }
        scan_values = {name: values[name] for name in SCAN_FIELD_ORDER if name in values}
        return service_values, post_processing_values, scan_values

    def _read_values(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(content_length)
        content_type = self.headers.get("Content-Type", "").lower()
        if content_type.startswith("application/json"):
            try:
                values = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("request body must contain a JSON object") from exc
            if not isinstance(values, dict):
                raise ValueError("request body must contain a JSON object")
            return values

        parsed = parse_qs(payload.decode("utf-8"), keep_blank_values=True)
        return {name: values[-1] for name, values in parsed.items()}

    def _send_json(self, status: HTTPStatus, data: object) -> None:
        body = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_admin_handler(
    service_settings_store: ServiceSettingsStore,
    post_processing_store: PostProcessingSettingsStore,
    scan_ticket_store: ScanTicketStore,
) -> type[AdminRequestHandler]:
    """Create a request handler class bound to a scan ticket store."""

    class ConfiguredAdminRequestHandler(AdminRequestHandler):
        pass

    ConfiguredAdminRequestHandler.service_settings_store = service_settings_store
    ConfiguredAdminRequestHandler.post_processing_store = post_processing_store
    ConfiguredAdminRequestHandler.scan_ticket_store = scan_ticket_store
    return ConfiguredAdminRequestHandler


class AdminService:
    """Threaded web server for scan ticket configuration."""

    def __init__(
        self,
        service_settings_store: ServiceSettingsStore,
        post_processing_store: PostProcessingSettingsStore,
        scan_ticket_store: ScanTicketStore,
        *,
        port: int = ADMIN_PORT,
        server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServerNoFqdn,
    ) -> None:
        self.port = port
        handler = make_admin_handler(
            service_settings_store,
            post_processing_store,
            scan_ticket_store,
        )
        self.servers: list[ThreadingHTTPServer] = [server_factory(("", port), handler)]
        try:
            self.servers.append(ThreadingHTTPServerV6(("::", port), handler))
        except OSError:
            LOGGER.warning("IPv6 admin web UI unavailable", exc_info=True)
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for index, server in enumerate(self.servers):
            family = "ipv6" if server.address_family == socket.AF_INET6 else "ipv4"
            thread = threading.Thread(
                target=server.serve_forever,
                name=f"wsd-admin-{family}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
            LOGGER.info(
                "admin web UI started",
                extra={"tcp_port": self.port, "family": family, "index": index},
            )

    def stop(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=2)
