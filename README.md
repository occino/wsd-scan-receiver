# WSD Scan Receiver

Self-hosted WSD/WS-Scan push receiver for network scanners.

The service appears on the LAN as a "Scan to Computer (WSD)" destination and
saves received scans into a configurable output directory, commonly a
Paperless-ngx consume directory. It is designed to run in Docker with host
networking and can write directly to another application's scan import
directory.

## Status

This project is usable, but WSD push scanning is vendor-sensitive. It has been
developed against an Epson ET-2750 and should still be treated as experimental
for other models and firmware versions.

Implemented:

- WS-Discovery listener on UDP `3702`
- WS-Discovery `Hello`, `Probe`, and `Resolve` handling
- HTTP SOAP/DPWS endpoint on TCP `5357`
- Web UI for scan ticket settings on TCP `8888` by default
- Optional document cropping with automatic, fixed DIN-A4, and disabled modes
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

Copy and edit the example files:

```bash
cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
```

Set at least:

```dotenv
SCAN_DIR=/path/to/scan-output
WSD_DEVICE_NAME=Paperless
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

Open the scan settings page:

```bash
xdg-open "http://127.0.0.1:${WSD_ADMIN_PORT:-8888}/"
```

## Docker Networking

The compose file uses `network_mode: host` intentionally. WS-Discovery relies on
multicast traffic to `239.255.255.250:3702` and, on some networks, IPv6
link-local multicast. Docker bridge networking often prevents scanners on the
LAN from seeing the receiver.

Required traffic:

- UDP `3702` inbound and outbound for WS-Discovery
- TCP `5357` inbound from the scanner for SOAP/DPWS callbacks
- TCP `${WSD_ADMIN_PORT:-8888}` inbound from trusted LAN clients for the settings page
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
| `WSD_ADMIN_PORT` | `8888` | TCP port for the settings web UI |
| `OUTPUT_DIR` | `/scans` | Directory where scans are written inside the container |
| `ORIGINAL_DIR` | `/original` | Directory where `Keep original` stores copies inside the container |
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
| `SCAN_DIR` | `./scans` | Host directory mounted as `/scans` |
| `ORIGINAL_DIR` | `./original` | Host directory mounted as `/original` |
| `DEBUG_DUMPS_DIR` | `./debug-dumps` | Host directory mounted as `/debug-dumps` |
| `DATA_DIR` | `./data` | Host directory mounted as `/data` |
| `PUID` | `1000` | Container user ID for file writes |
| `PGID` | `1000` | Container group ID for file writes |
| `WSD_DEBUG` | `false` | Compose-friendly alias passed to application `DEBUG` |

### Document Cropping

Document cropping is configured in the web UI under `Document Cropping`.
Supported image files can be automatically cropped with Python/Pillow before the
final file is moved into `OUTPUT_DIR`. Unsupported payloads, such as PDFs, are
stored unchanged.

The crop behavior assumes the document is aligned with the top-left scanner bed
corner. In `none` mode no cropping is applied. In `auto` mode it detects the
free side and bottom edges, then keeps the fixed top-left corner anchored. In
`DIN-A4` mode it crops a fixed top-left A4 rectangle and ignores the auto tuning
parameters below. The behavior can be tuned in the same section:

| Setting | Default | Description |
| --- | --- | --- |
| `Crop mode` | `DIN-A4` | `none` skips cropping, `auto` uses automatic document detection, `DIN-A4` crops a fixed A4 rectangle |
| `Background threshold` | `220` | Corner brightness at or above this value disables auto-cropping |
| `Document contrast` | `35` | Brightness difference from the detected background needed to identify the document |
| `Minimum document width (%)` | `50` | Ignore detected crop boxes narrower than this share of the image |
| `Minimum document height (%)` | `50` | Ignore detected crop boxes shorter than this share of the image |
| `Side crop padding (px)` | `20` | Pixels added back on the detected free side; for top-left alignment this is the right edge |
| `Bottom crop padding (px)` | `20` | Pixels added back below the detected document |

### Scan Ticket Parameters

These values are sent in the WS-Scan `CreateScanJob` request. They are no
longer configured with `.env` or `SCAN_*` variables. Open
`http://127.0.0.1:${WSD_ADMIN_PORT:-8888}/` and save the form to write
`/data/config.json`.
Changes apply to the next scan job without restarting the service.
The `Keep original` service parameter defaults to `false` and stores a copy of
the final scan file in `ORIGINAL_DIR`, including the cropped file when document
cropping is active.

Defaults are shipped in `src/wsd_scan_receiver/scan_defaults.json`. The current
defaults are Epson ET-2750 values observed to work. Other scanners may reject a
job when a value is unsupported, so change one setting at a time and keep
`WSD_DEBUG=true` while testing.

The same data is available as JSON:

```bash
curl "http://127.0.0.1:${WSD_ADMIN_PORT:-8888}/api/scan-config"
```

## Paperless-ngx

The most common setup is to let this receiver write directly into the
Paperless-ngx consume directory. In that case, both containers should reference
the same host directory:

- Paperless-ngx mounts it as its consume directory, usually `/usr/src/paperless/consume`.
- WSD Scan Receiver mounts the same host directory as `/scans` through `SCAN_DIR`.

Example `.env` for this project:

```dotenv
SCAN_DIR=/srv/paperless/consume
PUID=1000
PGID=1000
```

Matching Paperless-ngx compose volume:

```yaml
services:
  paperless-webserver:
    volumes:
      - /srv/paperless/consume:/usr/src/paperless/consume
```

With the default `docker-compose.yml.example`, the receiver then mounts the same
directory like this:

```yaml
volumes:
  - ${SCAN_DIR:-./scans}:/scans
```

The receiver writes timestamped files such as `scan-20260706T170000.000000Z-abc12345.jpg`.
Paperless-ngx should import them on its normal scan import schedule. If files
appear but Paperless does not pick them up, check ownership, Paperless logs, and
whether the Paperless container sees the file inside
`/usr/src/paperless/consume`.

`Keep original` is separate from the Paperless consume flow. When enabled, it
stores a copy in `ORIGINAL_DIR`; that directory should usually not be mounted as
the Paperless consume directory, otherwise Paperless may import both the final
scan and the retained copy.

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

- Confirm `SCAN_DIR` exists and is writable by `PUID:PGID`.
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
DEBUG=true OUTPUT_DIR=./scans RAW_DUMP_DIR=./debug-dumps WSD_HOST=<your-lan-ip> \
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
