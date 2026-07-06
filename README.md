# WSD Scan Receiver

Self-hosted WSD/WS-Scan push receiver for network scanners.

The service appears on the LAN as a "Scan to Computer (WSD)" destination and
saves received scans into a configurable directory, typically the Paperless-ngx
consume folder. It is designed to run in Docker with host networking.

## Status

This project is usable, but WSD push scanning is vendor-sensitive. It has been
developed against an Epson ET-2750 and should still be treated as experimental
for other models and firmware versions.

Implemented:

- WS-Discovery listener on UDP `3702`
- WS-Discovery `Hello`, `Probe`, and `Resolve` handling
- HTTP SOAP/DPWS endpoint on TCP `5357`
- DPWS metadata responses for WS-Transfer and WS-MetadataExchange
- Active WS-Eventing subscription to scanner `ScanAvailableEvent` notifications
- WS-Scan push flow: `ScanAvailableEvent` -> `CreateScanJob` -> `RetrieveImage`
- MTOM/XOP multipart image extraction
- Direct PDF/JPEG/PNG/TIFF payload storage
- Stable persisted WSD UUID
- Structured JSON logging
- Debug dumps for incoming POSTs, outgoing SOAP requests, image retrieval
  responses, and SOAP error responses
- Health endpoint at `/healthz`

Known limits:

- Full WSD/WS-Scan compatibility is not guaranteed.
- Scanner profile negotiation is intentionally conservative.
- Current default scan ticket values are based on an Epson ET-2750 WSD capture.
- Multi-page jobs and richer job status tracking are not fully implemented.
- The service is intended for trusted LANs, not direct exposure to the internet.

## Quick Start

Copy and edit the environment file:

```bash
cp .env.example .env
```

Set at least:

```dotenv
CONSUME_DIR=/path/to/paperless/consume
WSD_DEVICE_NAME=Paperless
WSD_HOST=192.168.0.8
WSD_INTERFACE=ens16
WSD_SUBSCRIBE_ENABLED=true
WSD_SCANNER_IP=192.168.0.21
```

Start the service:

```bash
docker compose up --build -d
```

Check status:

```bash
docker compose ps
docker compose logs -f
curl http://127.0.0.1:5357/healthz
```

## Docker Networking

The compose file uses `network_mode: host` intentionally. WS-Discovery relies on
multicast traffic to `239.255.255.250:3702` and, on some networks, IPv6
link-local multicast. Docker bridge networking often prevents scanners on the
LAN from seeing the receiver.

Required traffic:

- UDP `3702` inbound and outbound for WS-Discovery
- TCP `5357` inbound from the scanner for SOAP/DPWS callbacks
- HTTP from the receiver to the scanner for WS-Eventing and WS-Scan requests

## Configuration

Environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `WSD_DEVICE_NAME` | `Paperless WSD Scanner` | Name shown on the scanner |
| `WSD_HOSTNAME` | `paperless-wsd` | Container hostname used by Compose |
| `WSD_UUID` | generated | Stable endpoint ID, preferred format `urn:uuid:<uuid>` |
| `WSD_UUID_FILE` | `/data/wsd-uuid` | File used to persist generated UUID |
| `WSD_HTTP_PORT` | `5357` | TCP SOAP/DPWS HTTP port |
| `OUTPUT_DIR` | `/consume` | Directory where scans are written |
| `WSD_HOST` | auto-detected | IP advertised in WSD `XAddrs`; set this on multi-homed hosts |
| `WSD_INTERFACE` | unset | LAN interface for IPv6 WS-Discovery, for example `ens16` |
| `WSD_SUBSCRIBE_ENABLED` | `false` | Actively subscribe this receiver as a WSD scan destination |
| `WSD_SUBSCRIBE_INTERVAL_SECONDS` | `60` | Probe/subscription refresh interval |
| `WSD_SCANNER_IP` | unset | Optional scanner IP for directed WSD probing when multicast is unreliable |
| `MAX_POST_BYTES` | `104857600` | Maximum accepted incoming HTTP POST size |
| `DEBUG` | `false` | Enables verbose discovery/SOAP logging and raw dumps |
| `RAW_DUMP_DIR` | `/debug-dumps` | Debug dump directory |
| `LOG_LEVEL` | `INFO` | Python log level |

`EPSON_PRINTER_IP` is still accepted by the application as a legacy fallback,
but new deployments should use `WSD_SCANNER_IP`.

Compose-specific `.env` variables:

| Variable | Default | Description |
| --- | --- | --- |
| `CONSUME_DIR` | `./consume` | Host directory mounted as `/consume` |
| `DEBUG_DUMPS_DIR` | `./debug-dumps` | Host directory mounted as `/debug-dumps` |
| `DATA_DIR` | `./data` | Host directory mounted as `/data` |
| `PUID` | `1000` | Container user ID for file writes |
| `PGID` | `1000` | Container group ID for file writes |
| `WSD_DEBUG` | `false` | Compose-friendly alias passed to application `DEBUG` |

### Scan Ticket Parameters

These values are sent in the WS-Scan `CreateScanJob` request. The defaults are
the Epson ET-2750 values that were observed to work. Other scanners may reject a
job when a value is unsupported, so change one setting at a time and keep
`WSD_DEBUG=true` while testing.

| Variable | Default | Known/typical values | Description |
| --- | --- | --- | --- |
| `SCAN_FORMAT` | `exif` | `exif`, `tiff-single-uncompressed` | Requested output format. Epson ET-2750 returned JPEG/Exif for `exif`. |
| `SCAN_INPUT_SOURCE` | `Auto` | `Auto`, `Platen` | Input source used when the scanner event does not provide one. |
| `SCAN_CONTENT_TYPE` | `Text` | `Text`, `Photo`, `Mixed` | Scanner image optimization hint. |
| `SCAN_COLOR_PROCESSING` | `RGB24` | `RGB24`, `Grayscale8`, `BlackAndWhite1` | Color mode. Epson ET-2750 testing confirmed `RGB24`. |
| `SCAN_RESOLUTION` | `100` | `100`, `300` | Horizontal and vertical DPI. Epson ET-2750 reported `100` and `300`. |
| `SCAN_COMPRESSION_QUALITY` | `50` | `1`-`100` | Compression quality hint for compressed formats such as `exif`. |
| `SCAN_IMAGES_TO_TRANSFER` | `1` | usually `1` | Number of images requested for the job. Multi-page retrieval is still limited. |
| `SCAN_WIDTH` | `8500` | scanner capability value | Input media width in WSD units. |
| `SCAN_HEIGHT` | `11700` | scanner capability value | Input media height in WSD units. |
| `SCAN_REGION_X` | `0` | scanner capability value | Scan region X offset. |
| `SCAN_REGION_Y` | `0` | scanner capability value | Scan region Y offset. |
| `SCAN_REGION_WIDTH` | `8500` | scanner capability value | Scan region width. |
| `SCAN_REGION_HEIGHT` | `11700` | scanner capability value | Scan region height. |
| `SCAN_BRIGHTNESS` | `0` | scanner-dependent signed integer | Brightness adjustment; `0` is neutral. |
| `SCAN_CONTRAST` | `0` | scanner-dependent signed integer | Contrast adjustment; `0` is neutral. |
| `SCAN_SHARPNESS` | `0` | scanner-dependent signed integer | Sharpness adjustment; `0` is neutral. |
| `SCAN_ROTATION` | `0` | `0`, `90`, `180`, `270` | Rotation requested from the scanner. |
| `SCAN_SCALING_WIDTH` | `100` | percent | Horizontal scaling. `100` means no scaling. |
| `SCAN_SCALING_HEIGHT` | `100` | percent | Vertical scaling. `100` means no scaling. |

## Paperless-ngx

Point `CONSUME_DIR` at a Paperless-ngx consume directory:

```dotenv
CONSUME_DIR=/srv/paperless/consume
PUID=1000
PGID=1000
```

The receiver writes timestamped files such as `scan-20260706T170000.000000Z-abc12345.jpg`.
Paperless-ngx should import them on its normal consume schedule. If files appear
but Paperless does not consume them, check ownership and Paperless logs.

## How It Works

The service has three cooperating parts:

- `discovery.py` advertises the computer destination with WS-Discovery.
- `receiver.py` serves DPWS metadata, receives scanner events, and stores direct
  binary payloads.
- `ws_scan_client.py` actively discovers scanner services, subscribes to scan
  events, creates scan jobs, retrieves images, and stores extracted image parts.

Most successful push scans follow this flow:

1. The receiver announces itself and responds to discovery probes.
2. The active client subscribes to the scanner's `ScanAvailableEvent`.
3. The scanner shows `WSD_DEVICE_NAME` in its "Scan to Computer (WSD)" menu.
4. The scanner posts a `ScanAvailableEvent` to `/events`.
5. The receiver sends `CreateScanJob` to the scanner.
6. The receiver sends `RetrieveImage` and stores the returned image payload.

Unknown SOAP actions return a SOAP fault and are logged. The receiver no longer
returns fabricated scan jobs for unsupported actions.

## Testing Discovery

Watch discovery traffic:

```bash
sudo tcpdump -ni any udp port 3702
```

Send a test probe:

```bash
socat - UDP-DATAGRAM:239.255.255.250:3702,ip-multicast-ttl=2 < examples/wsd-probe.xml
```

In the tcpdump output you should see a `ProbeMatches` response from the service.

## Capturing Traffic

Capture the WSD flow while opening the scanner's WSD menu or starting a scan:

```bash
sudo tcpdump -i any -w wsd-scan.pcap 'udp port 3702 or tcp port 5357'
```

Useful things to inspect in Wireshark:

- Whether scanner probes or hello packets arrive on the host
- Which discovery namespace, types, scopes, and XAddrs the scanner uses
- Whether the receiver subscribes successfully
- Whether the scanner posts `ScanAvailableEvent`
- Whether `CreateScanJob` and `RetrieveImage` receive successful responses
- Whether image data is plain binary, SOAP body content, or MTOM/XOP multipart

For deeper analysis set:

```dotenv
WSD_DEBUG=true
```

Debug mode writes raw bodies to `DEBUG_DUMPS_DIR`. These files may contain scan
content and device identifiers, so handle them as private data.

## Troubleshooting

Scanner does not show the receiver:

- Use Docker host networking.
- Confirm scanner and host are in the same LAN/VLAN.
- Allow UDP `3702` in the host firewall.
- Set `WSD_HOST` to the host's LAN IP if the advertised URL is wrong.
- Set `WSD_INTERFACE` to the LAN interface for IPv6 discovery.
- Enable `WSD_SUBSCRIBE_ENABLED=true`; many scanners only show subscribed
  destinations.
- Set `WSD_SCANNER_IP` if multicast discovery from the scanner is unreliable.
- Restart the scanner after changing discovery or subscription settings.

Receiver appears, but scan does not complete:

- Confirm TCP `5357` is reachable from the scanner.
- Check logs for `ScanAvailableEvent`, `CreateScanJob`, and `RetrieveImage`.
- Enable `WSD_DEBUG=true` and inspect debug dumps.
- Capture `udp port 3702 or tcp port 5357` and compare the flow with the logs.
- Increase `MAX_POST_BYTES` if the scan payload is larger than the default.

Files are not written:

- Confirm `CONSUME_DIR` exists and is writable by `PUID:PGID`.
- Check container logs for permission errors.
- Confirm `/healthz` responds.
- Temporarily enable `WSD_DEBUG=true` to verify that HTTP POSTs arrive.

Duplicate devices on the scanner:

- Use a stable `WSD_UUID` or persist `/data`.
- Avoid running multiple receiver instances on the same LAN.
- Restart the scanner to clear stale WSD destinations.

## Development

Install locally:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

Run locally:

```bash
DEBUG=true OUTPUT_DIR=./consume RAW_DUMP_DIR=./debug-dumps WSD_HOST=<your-lan-ip> \
  WSD_SUBSCRIBE_ENABLED=true WSD_SCANNER_IP=<scanner-ip> \
  python -m wsd_scan_receiver.main
```

Before opening a pull request or cutting a release:

```bash
pytest
ruff check .
python -m compileall src tests
docker compose up --build -d --force-recreate --remove-orphans
```

Good next extensions:

- Named scan ticket profiles for switching between common scanner presets.
- Multi-page job retrieval and richer status tracking.
- Metrics endpoint for subscriptions, scan jobs, and failures.
- Integration tests around recorded scanner SOAP fixtures.
