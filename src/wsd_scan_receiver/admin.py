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
    FIXED_CROP_MODE_SIZES_MM,
    LOG_LEVELS,
    POST_PROCESSING_FIELDS,
    SCAN_TICKET_ALLOWED_VALUES,
    SCAN_TICKET_POSITIVE_INT_FIELDS,
    SCAN_TICKET_TEXT_FIELDS,
    SERVICE_SETTINGS_FIELDS,
    UI_SETTINGS_FIELDS,
    PostProcessingSettings,
    PostProcessingSettingsStore,
    ScanTicketStore,
    ServiceSettingsStore,
    UiSettings,
    UiSettingsStore,
    load_scan_ticket_defaults,
    load_service_settings_defaults,
    post_processing_settings_from_mapping,
    post_processing_settings_to_dict,
    scan_ticket_from_mapping,
    scan_ticket_to_dict,
    service_settings_from_mapping,
    service_settings_to_dict,
    ui_settings_from_mapping,
    ui_settings_to_dict,
)
from .receiver import ThreadingHTTPServerNoFqdn, ThreadingHTTPServerV6

LOGGER = logging.getLogger(__name__)

ADMIN_PORT = 8888
MAX_ADMIN_POST_BYTES = 1024 * 1024


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
    "content_type": "Scanner image optimization hint. The ET-2750 WSD capabilities report Text.",
    "color_processing": (
        "Color mode requested from the scanner. Values: RGB24, Grayscale8, BlackAndWhite1."
    ),
    "resolution": "Horizontal and vertical scan resolution in DPI. Values: 100, 300.",
    "compression_quality": (
        "Compression quality for compressed formats. The ET-2750 WSD capabilities report 50."
    ),
    "images_to_transfer": "Number of images requested for one job. The ET-2750 default is 1.",
    "width": "Input media width in WSD units. ET-2750 platen maximum: 8500.",
    "height": "Input media height in WSD units. ET-2750 platen maximum: 11700.",
    "region_x": "Scan region X offset in WSD units. 0 starts at the left edge.",
    "region_y": "Scan region Y offset in WSD units. 0 starts at the top edge.",
    "region_width": "Scan region width in WSD units. ET-2750 platen maximum: 8500.",
    "region_height": "Scan region height in WSD units. ET-2750 platen maximum: 11700.",
    "brightness": "Scanner-dependent brightness adjustment. Integer; 0 is neutral.",
    "contrast": "Scanner-dependent contrast adjustment. Integer; 0 is neutral.",
    "sharpness": "Scanner-dependent sharpness adjustment. Integer; 0 is neutral.",
    "rotation": "Rotation requested from the scanner. The ET-2750 WSD capabilities report 0.",
    "scaling_width": "Horizontal scaling percentage. The ET-2750 WSD capabilities report 100.",
    "scaling_height": "Vertical scaling percentage. The ET-2750 WSD capabilities report 100.",
}
SCAN_FIELD_UI_OPTIONS = {
    "format": ["exif", "tiff-single-uncompressed"],
    "input_source": ["Auto", "Platen"],
    "content_type": ["Text"],
    "color_processing": ["RGB24", "Grayscale8", "BlackAndWhite1"],
    "resolution": ["100", "300"],
    "compression_quality": ["50"],
    "images_to_transfer": ["1"],
    "width": ["8500"],
    "height": ["11700"],
    "region_x": ["0"],
    "region_y": ["0"],
    "region_width": ["8500"],
    "region_height": ["11700"],
    "rotation": ["0"],
    "scaling_width": ["100"],
    "scaling_height": ["100"],
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
    "KEEP_ORIGINAL": "Keep original",
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
    "KEEP_ORIGINAL": (
        "When checked, store a copy of each final scan file in ORIGINAL_DIR."
    ),
    "DEBUG": "Enable verbose logging and raw debug dumps. Values: false, true.",
    "LOG_LEVEL": "Python log verbosity. Values: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET.",
}
SERVICE_FIELD_ORDER = [
    "WSD_DEVICE_NAME",
    "WSD_HOST",
    "WSD_INTERFACE",
    "WSD_SCANNER_IP",
    "KEEP_ORIGINAL",
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
        "or a paper format for a fixed top-left crop."
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
SHOW_FIXED_SCAN_FIELDS_NAME = "show_fixed_scan_parameters"
SHOW_FIXED_SCAN_FIELDS_HELP = (
    "When checked, scan parameters with fixed scanner capability values remain visible."
)


def _row(
    label: str,
    name: str,
    control: str,
    help_text: str,
    *,
    css_class: str = "",
) -> str:
    escaped_name = html.escape(name)
    escaped_label = html.escape(label)
    escaped_help = html.escape(help_text, quote=True)
    class_attr = f' class="{html.escape(css_class, quote=True)}"' if css_class else ""
    return (
        f"<tr{class_attr}><th><label for=\"{escaped_name}\">{escaped_label}</label>"
        f'<span class="help" tabindex="0" data-tooltip="{escaped_help}" '
        f'aria-label="{escaped_help}">?</span></th>'
        f"<td>{control}</td></tr>"
    )


def _select_for(name: str, value: str | int | bool, options: list[str]) -> str:
    escaped_name = html.escape(name)
    option_html = []
    selected_value = str(value).lower() if isinstance(value, bool) else str(value)
    disabled = " disabled" if len(options) == 1 else ""
    for option in options:
        selected = " selected" if str(option) == selected_value else ""
        escaped_option = html.escape(str(option))
        option_html.append(f'<option value="{escaped_option}"{selected}>{escaped_option}</option>')
    return (
        f'<select id="{escaped_name}" name="{escaped_name}"{disabled}>'
        f'{"".join(option_html)}</select>'
    )


def _checkbox_for(name: str, value: bool) -> str:
    escaped_name = html.escape(name)
    checked = " checked" if value else ""
    return (
        f'<input id="{escaped_name}" name="{escaped_name}" '
        f'type="checkbox" value="true"{checked}>'
    )


def _scan_input_for(name: str, value: str | int) -> str:
    label = SCAN_FIELD_LABELS[name]
    escaped_name = html.escape(name)
    ui_options = SCAN_FIELD_UI_OPTIONS.get(name)
    if ui_options is not None:
        control = _select_for(name, value, ui_options)
    elif name in SCAN_TICKET_TEXT_FIELDS or name in SCAN_TICKET_ALLOWED_VALUES:
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
    css_class = "fixed-scan-parameter" if ui_options is not None and len(ui_options) == 1 else ""
    return _row(label, name, control, SCAN_FIELD_HELP[name], css_class=css_class)


def _show_fixed_scan_fields_input(value: bool) -> str:
    control = _checkbox_for(SHOW_FIXED_SCAN_FIELDS_NAME, value)
    return _row(
        "Show parameters with fixed values",
        SHOW_FIXED_SCAN_FIELDS_NAME,
        control,
        SHOW_FIXED_SCAN_FIELDS_HELP,
    )


def _service_input_for(name: str, value: str | bool) -> str:
    label = SERVICE_FIELD_LABELS[name]
    escaped_name = html.escape(name)
    if name == "KEEP_ORIGINAL":
        control = _checkbox_for(name, bool(value))
    elif name == "DEBUG":
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
        control = _select_for(
            name,
            str(value),
            ["none", "auto", *FIXED_CROP_MODE_SIZES_MM.keys()],
        )
        return _row(label, name, control, POST_PROCESSING_FIELD_HELP[name])
    disabled = "" if crop_mode == "auto" else " disabled"
    css_class = "auto-crop-parameter" if crop_mode == "auto" else "auto-crop-parameter hidden"
    if name in {"background_threshold", "document_contrast"}:
        control = _number_input_for(name, int(value), minimum=0, maximum=255, extra=disabled)
    elif name in {"min_document_width_percent", "min_document_height_percent"}:
        control = _number_input_for(name, int(value), minimum=1, maximum=100, extra=disabled)
    else:
        control = _number_input_for(name, int(value), minimum=0, maximum=500, extra=disabled)
    return _row(label, name, control, POST_PROCESSING_FIELD_HELP[name], css_class=css_class)


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


def _page_json(data: object) -> str:
    return (
        json.dumps(data)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_index(
    service_values: dict[str, str | bool],
    post_processing_values: dict[str, bool | int | str],
    scan_values: dict[str, str | int],
    ui_values: dict[str, bool],
) -> bytes:
    default_values = {
        "service": service_settings_to_dict(load_service_settings_defaults()),
        "post_processing": post_processing_settings_to_dict(PostProcessingSettings()),
        "scan": scan_ticket_to_dict(load_scan_ticket_defaults()),
        "ui": ui_settings_to_dict(UiSettings()),
    }
    default_values_json = _page_json(default_values)
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
    scan_fields = "\n".join(
        [_show_fixed_scan_fields_input(ui_values["show_fixed_scan_parameters"])]
        + [_scan_input_for(name, scan_values[name]) for name in SCAN_FIELD_ORDER]
    )
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
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
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
    input[type="checkbox"] {{
      width: 18px;
      min-height: 18px;
      height: 18px;
      margin: 0;
      accent-color: var(--accent);
      vertical-align: middle;
    }}
    input:disabled, select:disabled {{
      background: color-mix(in srgb, var(--border) 22%, transparent);
      color: var(--muted);
      cursor: not-allowed;
      opacity: 0.65;
    }}
    .fixed-scan-parameter.hidden,
    .auto-crop-parameter.hidden {{
      display: none;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-left: auto;
    }}
    button {{
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 650;
      min-width: 82px;
      padding: 8px 16px;
      cursor: pointer;
      transition: background-color 120ms ease, opacity 120ms ease;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.92;
    }}
    button.saved {{
      background: #16823a;
    }}
    button.error {{
      background: #b42318;
    }}
    button.secondary-button {{
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
    }}
    @media (max-width: 640px) {{
      .topbar {{
        align-items: flex-start;
      }}
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
    <form method="post" action="/api/config">
      <div class="topbar">
        <h1>WSD Scan Receiver</h1>
        <div class="actions">
          <button id="restore_defaults_button" class="secondary-button" type="button">
            Restore defaults
          </button>
          <button id="save_button" type="submit">Save</button>
        </div>
      </div>
      {service_section}
      {scan_section}
      {post_processing_section}
    </form>
    <script id="default_values" type="application/json">{default_values_json}</script>
    <script>
      const form = document.querySelector('form');
      const saveButton = document.querySelector('#save_button');
      const restoreDefaultsButton = document.querySelector('#restore_defaults_button');
      const defaultValues = JSON.parse(
        document.querySelector('#default_values').textContent
      );
      const cropMode = document.querySelector('#crop_mode');
      const showFixedScanParameters = document.querySelector('#show_fixed_scan_parameters');
      const fixedScanRows = Array.from(document.querySelectorAll('.fixed-scan-parameter'));
      const autoCropRows = Array.from(document.querySelectorAll('.auto-crop-parameter'));
      const autoControls = Array.from(document.querySelectorAll(
        '#background_threshold, #document_contrast, #min_document_width_percent, '
        + '#min_document_height_percent, #crop_side_padding, #crop_bottom_padding'
      ));
      function syncPostProcessingControls() {{
        const autoEnabled = cropMode.value === 'auto';
        for (const row of autoCropRows) {{
          row.classList.toggle('hidden', !autoEnabled);
        }}
        for (const control of autoControls) {{
          control.disabled = !autoEnabled;
        }}
      }}
      cropMode.addEventListener('change', syncPostProcessingControls);
      syncPostProcessingControls();

      function syncFixedScanRows() {{
        const visible = showFixedScanParameters.checked;
        for (const row of fixedScanRows) {{
          row.classList.toggle('hidden', !visible);
        }}
      }}
      showFixedScanParameters.addEventListener('change', syncFixedScanRows);
      syncFixedScanRows();

      function resetSaveButton() {{
        saveButton.classList.remove('saved', 'error');
        saveButton.disabled = false;
        saveButton.textContent = 'Save';
      }}

      function setControlValue(name, value) {{
        const control = form.elements.namedItem(name);
        if (!control) {{
          return;
        }}
        if (control.type === 'checkbox') {{
          control.checked = Boolean(value);
          return;
        }}
        control.value = String(value);
      }}

      function restoreDefaults() {{
        for (const section of ['service', 'post_processing', 'scan', 'ui']) {{
          for (const [name, value] of Object.entries(defaultValues[section])) {{
            setControlValue(name, value);
          }}
        }}
        syncPostProcessingControls();
        syncFixedScanRows();
        resetSaveButton();
      }}
      restoreDefaultsButton.addEventListener('click', restoreDefaults);

      function setTemporaryButtonState(className, label, disabled) {{
        saveButton.classList.remove('saved', 'error');
        saveButton.classList.add(className);
        saveButton.disabled = disabled;
        saveButton.textContent = label;
        window.setTimeout(resetSaveButton, 1400);
      }}

      form.addEventListener('submit', async (event) => {{
        event.preventDefault();
        saveButton.disabled = true;
        saveButton.textContent = 'Saving';
        const disabledControls = Array.from(form.querySelectorAll(':disabled'));
        for (const control of disabledControls) {{
          control.disabled = false;
        }}
        const body = new URLSearchParams(new FormData(form));
        for (const control of disabledControls) {{
          control.disabled = true;
        }}
        try {{
          const response = await fetch('/api/config', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body,
          }});
          if (!response.ok) {{
            throw new Error('save failed');
          }}
          setTemporaryButtonState('saved', 'Saved', true);
        }} catch (_error) {{
          setTemporaryButtonState('error', 'Error', false);
        }}
      }});
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
    ui_settings_store: UiSettingsStore

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
                    self.ui_settings_store.as_dict(),
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
                    "ui": self.ui_settings_store.as_dict(),
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
                ui_settings = self.ui_settings_store.get()
            else:
                service_values, post_processing_values, scan_values, ui_values = (
                    self._split_values(values)
                )
                service_settings_from_mapping(
                    service_values,
                    base=self.service_settings_store.get(),
                )
                post_processing_settings_from_mapping(
                    post_processing_values,
                    base=self.post_processing_store.get(),
                )
                scan_ticket_from_mapping(scan_values, base=self.scan_ticket_store.get())
                ui_settings_from_mapping(ui_values, base=self.ui_settings_store.get())
                service_settings = self.service_settings_store.update(service_values)
                post_processing = self.post_processing_store.update(post_processing_values)
                ui_settings = self.ui_settings_store.update(ui_values)
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
                        self.ui_settings_store.as_dict(),
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
                        "ui": ui_settings_to_dict(ui_settings),
                    },
                )
            return
        self._send(
            HTTPStatus.OK,
            render_index(
                service_settings_to_dict(service_settings),
                post_processing_settings_to_dict(post_processing),
                scan_ticket_to_dict(scan_ticket),
                ui_settings_to_dict(ui_settings),
            ),
            "text/html; charset=utf-8",
        )

    def _split_values(
        self,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        if (
            "service" in values
            or "post_processing" in values
            or "scan" in values
            or "ui" in values
        ):
            service_values = values.get("service", {})
            post_processing_values = values.get("post_processing", {})
            scan_values = values.get("scan", {})
            ui_values = values.get("ui", {})
            if (
                not isinstance(service_values, dict)
                or not isinstance(post_processing_values, dict)
                or not isinstance(scan_values, dict)
                or not isinstance(ui_values, dict)
            ):
                raise ValueError(
                    "service, post_processing, scan, and ui must contain JSON objects"
                )
            return service_values, post_processing_values, scan_values, ui_values
        service_values = {name: values[name] for name in SERVICE_SETTINGS_FIELDS if name in values}
        if "KEEP_ORIGINAL" not in service_values:
            service_values["KEEP_ORIGINAL"] = "false"
        post_processing_values = {
            name: values[name] for name in POST_PROCESSING_FIELDS if name in values
        }
        scan_values = {name: values[name] for name in SCAN_FIELD_ORDER if name in values}
        ui_values = {name: values[name] for name in UI_SETTINGS_FIELDS if name in values}
        if "show_fixed_scan_parameters" not in ui_values:
            ui_values["show_fixed_scan_parameters"] = "false"
        return service_values, post_processing_values, scan_values, ui_values

    def _read_values(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if content_length < 0:
            raise ValueError("Content-Length must not be negative")
        if content_length > MAX_ADMIN_POST_BYTES:
            raise ValueError(f"request body must not exceed {MAX_ADMIN_POST_BYTES} bytes")
        payload = self.rfile.read(content_length)
        content_type = self.headers.get("Content-Type", "").lower()
        if content_type.startswith("application/json"):
            try:
                values = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must contain a JSON object") from exc
            if not isinstance(values, dict):
                raise ValueError("request body must contain a JSON object")
            return values

        try:
            parsed = parse_qs(payload.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError as exc:
            raise ValueError("request body must be valid UTF-8") from exc
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
    ui_settings_store: UiSettingsStore,
) -> type[AdminRequestHandler]:
    """Create a request handler class bound to a scan ticket store."""

    class ConfiguredAdminRequestHandler(AdminRequestHandler):
        pass

    ConfiguredAdminRequestHandler.service_settings_store = service_settings_store
    ConfiguredAdminRequestHandler.post_processing_store = post_processing_store
    ConfiguredAdminRequestHandler.scan_ticket_store = scan_ticket_store
    ConfiguredAdminRequestHandler.ui_settings_store = ui_settings_store
    return ConfiguredAdminRequestHandler


class AdminService:
    """Threaded web server for scan ticket configuration."""

    def __init__(
        self,
        service_settings_store: ServiceSettingsStore,
        post_processing_store: PostProcessingSettingsStore,
        scan_ticket_store: ScanTicketStore,
        ui_settings_store: UiSettingsStore,
        *,
        port: int = ADMIN_PORT,
        server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServerNoFqdn,
    ) -> None:
        self.port = port
        handler = make_admin_handler(
            service_settings_store,
            post_processing_store,
            scan_ticket_store,
            ui_settings_store,
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
