# epson-scan-receiver

Experimental self-hosted WSD/WS-Scan push receiver for network scanners.

The goal is to make a small Docker service appear on the LAN as a "Scan to
Computer (WSD)" target. When a scanner such as an Epson ET-2750 can push a scan
to it, the service saves the resulting payload into a configurable directory,
for example a Paperless-ngx consume folder.

## Status

This project is intentionally experimental.

Implemented today:

- UDP WS-Discovery listener on port `3702`
- Basic WS-Discovery `Probe` parsing and `ProbeMatches` responses
- HTTP SOAP/DPWS endpoint on port `5357`
- Basic metadata responses for WS-Transfer and WS-MetadataExchange requests
- Experimental routing for a few common WS-Scan action names
- Raw POST dumps in debug mode
- Direct binary/PDF/image POST payload storage in `OUTPUT_DIR`

Not guaranteed yet:

- Full compatibility with any scanner model or firmware
- Complete WS-Scan job negotiation
- MTOM/XOP attachment extraction
- Vendor-specific Epson behavior

WSD push behavior varies by scanner and vendor. Real compatibility will likely
need packet captures from the scanner you want to support.

## Configuration

Environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `WSD_DEVICE_NAME` | `Paperless WSD Scanner` | Name shown to scanners during discovery |
| `WSD_HOSTNAME` | `paperless-wsd` | Docker container hostname; EpsonScan2 may use this as the front-panel computer name |
| `WSD_UUID` | generated | Stable WSD endpoint UUID; preferred format is `urn:uuid:<uuid>`; persisted in `/data/wsd-uuid` when possible |
| `WSD_UUID_FILE` | `/data/wsd-uuid` | File used for generated UUID persistence |
| `WSD_HTTP_PORT` | `5357` | TCP port for SOAP/DPWS HTTP requests |
| `OUTPUT_DIR` | `/consume` | Directory where received scan payloads are written |
| `DEBUG` | `false` | Enables verbose SOAP/discovery logging and raw POST dumps |
| `RAW_DUMP_DIR` | `/debug-dumps` | Directory for debug dumps |
| `LOG_LEVEL` | `INFO` | Python log level |
| `WSD_HOST` | auto-detected | Optional override for the IP advertised in discovery `XAddrs` |
| `WSD_INTERFACE` | unset | Optional LAN interface for IPv6 WS-Discovery multicast, for example `ens16` |
| `WSD_SUBSCRIBE_ENABLED` | `false` | Experimental: actively probes WSD scanners and tries WS-Eventing subscription |
| `WSD_SUBSCRIBE_INTERVAL_SECONDS` | `60` | Interval for active WSD scan-device probes/subscription attempts |
| `EPSON_PRINTER_IP` | unset | Optional Epson device IP for experimental UDP `3289` discovery polling in debug mode |
| `EPSONSCAN2_ENABLED` | `false` | Enables the optional native EpsonScan2 push-scan ready bridge |
| `EPSONSCAN2_LIB_HOST_DIR` | `./epsonscan2-lib` | Compose-only host path containing EpsonScan2 native libraries |
| `EPSONSCAN2_LIB_DIR` | unset | Container path containing `libes2command.so`, for example `/epsonscan2-lib` |
| `EPSONSCAN2_LIB_PATH` | unset | Direct path to `libes2command.so`, overrides directory lookup |
| `EPSONSCAN2_KEEPALIVE` | `true` | Keeps the EpsonScan2 connection open after setting panel push-scan ready |
| `EPSONSCAN2_REFRESH_SECONDS` | `0` | Experimental: periodically toggles EpsonScan2 push-ready off/on if a scanner forgets the ready state |

The compose file also reads `PUID` and `PGID` from `.env` so files written to
bind-mounted directories are owned by a useful host user.

## Run With Docker Compose

Copy the example environment file and adjust the host paths:

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

The compose file uses `network_mode: host`. WS-Discovery relies on UDP multicast
to `239.255.255.250:3702`, and host networking avoids the common Docker bridge
network issue where multicast from the LAN never reaches the container.

The default compose file mounts:

- `./consume:/consume`
- `./debug-dumps:/debug-dumps`
- `./data:/data`

To feed Paperless-ngx directly, set `CONSUME_DIR` in `.env`:

```dotenv
CONSUME_DIR=/path/to/paperless/consume
DEBUG_DUMPS_DIR=./debug-dumps
DATA_DIR=./data
PUID=1000
PGID=1000
WSD_DEVICE_NAME=Paperless WSD Scanner
WSD_HOSTNAME=paperless-wsd
WSD_HOST=
WSD_INTERFACE=
WSD_SUBSCRIBE_ENABLED=false
WSD_SUBSCRIBE_INTERVAL_SECONDS=60
EPSON_PRINTER_IP=
EPSONSCAN2_ENABLED=false
EPSONSCAN2_LIB_HOST_DIR=./epsonscan2-lib
EPSONSCAN2_LIB_DIR=/epsonscan2-lib
EPSONSCAN2_LIB_PATH=
EPSONSCAN2_KEEPALIVE=true
EPSONSCAN2_REFRESH_SECONDS=0
WSD_DEBUG=false
LOG_LEVEL=INFO
```

## How It Works

The service starts two listeners:

- UDP `3702` for WS-Discovery `Probe` messages
- TCP `5357` for HTTP SOAP/DPWS requests

When `WSD_SUBSCRIBE_ENABLED=true`, it also behaves like an experimental WSD scan
client: it sends active scan-device probes and attempts a WS-Eventing
`Subscribe` to discovered scanner service addresses. This is the path Windows
uses before a scanner can send `ScanAvailableEvent` push notifications.

Discovery responses advertise the configured device name and metadata/scanner
URLs. The HTTP server exposes basic metadata at `/`, `/metadata`, `/device`, and
`/scanner`, and accepts SOAP or binary POSTs at the same server.

If a POST body looks like PDF/JPEG/PNG/TIFF or another binary payload, it is
saved as `scan-<timestamp>-<id>.<ext>` in `OUTPUT_DIR`.

If a POST body looks like SOAP/XML, the service parses the SOAP envelope and
routes known action names. Unknown SOAP actions return a SOAP fault and are
logged instead of crashing the process.

With `DEBUG=true`, every incoming POST is also written to `RAW_DUMP_DIR`.
Debug mode also starts experimental Epson traffic listeners on UDP `3289`,
UDP/TCP `2968`, and TCP `1865`. These are not a complete Epson Event Manager
implementation; they exist to capture whether Epson push-scan discovery is
reaching the host. If `EPSON_PRINTER_IP` is set, debug mode also sends the
observed Epson UDP `3289` discovery query periodically and opens a capture-driven
TCP `1865` scanner-status session similar to Epson's macOS software.

### Optional EpsonScan2 Push-Scan Bridge

Some Epson models appear to require EpsonScan2's native
`SetPanelToPushScanReady(true)` state before the front-panel "Scan to Computer"
target is shown. The image includes a small helper, `epsonscan2-push-ready`,
which dynamically loads Epson's `libes2command.so` and calls that native API.

This is disabled by default because EpsonScan2 is not redistributed in this
image. To test it, install or build EpsonScan2 on the host, mount the directory
that contains `libes2command.so`, then set:

```dotenv
EPSONSCAN2_ENABLED=true
EPSONSCAN2_LIB_HOST_DIR=/path/to/epsonscan2/lib
EPSONSCAN2_LIB_DIR=/epsonscan2-lib
EPSONSCAN2_REFRESH_SECONDS=20
```

If the bridge starts successfully, logs should show
`epsonscan2_push_scan_ready_set`. With refresh enabled, logs should also show
`epsonscan2_push_scan_ready_refreshed`. If it logs that `libes2command.so` is missing,
the mount path or `EPSONSCAN2_LIB_DIR` is wrong.

## Test Discovery

On a Linux host, first start the service with host networking, then watch for
traffic:

```bash
sudo tcpdump -ni any udp port 3702
```

You can also send a simple probe with `socat`:

```bash
socat - UDP-DATAGRAM:239.255.255.250:3702,ip-multicast-ttl=2 < examples/wsd-probe.xml
```

In another terminal:

```bash
sudo tcpdump -Ani any udp port 3702
```

You should see a `ProbeMatches` response from the service.

## Capture Scanner Traffic

For Wireshark or tcpdump analysis:

```bash
sudo tcpdump -i any -w wsd-scan.pcap 'udp port 3702 or tcp port 5357'
```

Then try `Scan -> To Computer (WSD)` on the scanner. Useful things to look for:

- Whether the scanner sends a `Probe` and receives `ProbeMatches`
- Which `Types`, `Scopes`, and `XAddrs` it expects
- Which SOAP `Action` headers it sends
- Whether scan data is sent as a plain HTTP payload, SOAP body content, or
  MTOM/XOP attachment

Set `DEBUG=true` while capturing so the service writes raw POST bodies to
`RAW_DUMP_DIR` as well.

## Troubleshooting

Scanner does not see the target:

- Use host networking on Docker.
- Confirm UDP `3702` is allowed by the host firewall.
- Confirm the scanner and Docker host are on the same LAN/VLAN.
- Set `WSD_HOST` if logs show the service advertising the wrong IP address.
- Run `tcpdump -ni any udp port 3702` and confirm probes arrive.

Discovery works, but no scan payload arrives:

- Confirm TCP `5357` is reachable from the scanner.
- Check the logs for SOAP faults or unknown actions.
- Enable `DEBUG=true` and inspect `debug-dumps/`.
- Capture `udp port 3702 or tcp port 5357` and compare the scanner's expected
  flow with the implemented handlers.

Files are not written:

- Confirm the mounted consume directory is writable by the container user.
- Check `OUTPUT_DIR` and compose volume paths.
- Temporarily run with `DEBUG=true` to see whether POST bodies arrive at all.

Paperless-ngx does not consume files:

- Mount Paperless-ngx's consume directory as `/consume`.
- Confirm Paperless has permission to read files created by this container.
- Check Paperless logs separately after a file appears in `/consume`.

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
  python -m wsd_scan_receiver.main
```

The protocol modules are split by responsibility:

- `discovery.py`: WS-Discovery UDP listener and `ProbeMatches` generation
- `soap.py`: SOAP envelope parsing and action routing
- `receiver.py`: HTTP server, metadata endpoints, raw dumps, payload writes
- `config.py`: environment parsing and UUID/IP handling

Good next extensions:

- Add handlers based on real Epson ET-2750 captures.
- Parse MTOM multipart requests and extract image attachments.
- Persist richer job state for `CreateScanJob`, status, and retrieval flows.
- Add configurable advertised scopes/types if another scanner expects them.
