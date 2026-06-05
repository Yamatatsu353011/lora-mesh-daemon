# lora-daemon

LoRa/BST-ID disaster-support mesh system の LoRa 制御デーモンです。

このコンポーネントは、EASEL ES920LR LoRaデバイスをPythonから制御し、自己位置/BST-IDビーコン送信、常時受信、Slotted ALOHA、将来的なFlooding、Duplicate Suppression、Implicit ACK、BST-ID指向性転送を担当します。

## Role

`lora-daemon` は、システム全体の中で以下を担当します。

* EASEL ES920LRのシリアル制御
* LoRa送受信
* ES920LR設定
* Slotted ALOHA型の周期送信
* 自己位置/BST-IDビーコン送信
* 受信した他ノード情報のRedis保存
* 今後のFlooding / Duplicate Suppression / Implicit ACK / BST-ID forwarding

業務ロジック、SQLite検索、支援物資問い合わせ、イベント管理などはこのコンポーネントには含めません。
それらは `lora-gateway-web` またはBusiness Driver側で扱います。

## System Position

```text
GPS / Redis state
  ↓
lora-daemon
  ↓
EASEL ES920LR
  ↓
LoRa mesh
  ↓
Other nodes
```

## Current Layer

Current implementation focuses on Layer 0/1:

```text
Layer 0:
  ES920LR open/configure/read/write

Layer 1:
  Slotted self beacon
  BST-ID beacon
  Redis lora:nodes:* update
```

Future layers:

```text
Layer 2:
  Packet / Token

Layer 3:
  Flooding + duplicate suppression

Layer 4:
  BST-ID directed forwarding

Layer 5:
  Implicit ACK and retry control

Layer 6:
  Business/Web driver interface
```

## Repository Layout

```text
config.py
  Node identity and ES920LR settings

easel_radio.py
  Minimal ES920LR serial driver

rx_test.py
  RX test script

tx_test.py
  TX test script

raw_daemon.py
  Redis raw TX/RX daemon

slotted_beacon_daemon.py
  Slotted ALOHA self-position/BST-ID beacon daemon

bst_codec.py
  Interface to bst-id library and BST-ID hex conversion
```

## Hardware

Expected hardware:

```text
Notebook PC / small desktop PC
or
Raspberry Pi Zero 2 W

EASEL ES920LR LoRa module
GPS/GNSS receiver
```

The LoRa device should be fixed as:

```text
/dev/lora0
```

using udev.

## udev Rule

For EASEL FT231X USB UART:

```bash
sudo nano /etc/udev/rules.d/99-lora-easel.rules
```

Example:

```udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", ATTRS{serial}=="DM01MXDO", SYMLINK+="lora0", GROUP="dialout", MODE="0660"
```

Reload:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Reconnect the device and check:

```bash
ls -l /dev/lora0
```

Add user to dialout:

```bash
sudo usermod -aG dialout $USER
```

Log out/in or reboot.

## Installation

```bash
cd /opt
sudo git clone https://github.com/yagu1/lora-daemon.git
sudo chown -R $USER:$USER /opt/lora-daemon

cd /opt/lora-daemon
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyserial redis python-dotenv
```

If using BST-ID:

```bash
cd /opt
sudo git clone https://github.com/yagu1/bst-id.git
sudo chown -R $USER:$USER /opt/bst-id

cd /opt/lora-daemon
source .venv/bin/activate
pip install -e /opt/bst-id
```

Check:

```bash
python -c "from bst_id.encoder import BSTIDEncoder; print('BST-ID OK')"
```

## Node Configuration

Edit:

```bash
nano config.py
```

Example for base node 1:

```python
NODE_ID = "yaglabterm01"
OWN_ID = "0001"
```

Example for base node 2:

```python
NODE_ID = "yaglabterm02"
OWN_ID = "0002"
```

Common radio settings:

```python
BW = "6"       # 500 kHz
SF = "8"       # spreading factor 8
CH = "1"       # channel 1, valid under 500 kHz

PAN_ID = "ABCD"
DST_ID = "FFFF"
ACK = "2"
RETRY = "0"
TRANSMODE = "1"
FORMAT = "1"
```

ES920LR command mapping:

```text
bw 6       500 kHz
sf 8       spreading factor 8
channel 1  channel 1
dstid FFFF broadcast
```

## Test RX

On receiver:

```bash
cd /opt/lora-daemon
source .venv/bin/activate
python rx_test.py
```

## Test TX

On transmitter:

```bash
cd /opt/lora-daemon
source .venv/bin/activate
python tx_test.py
```

Expected receiver output:

```text
[RX-LINE] T,yaglabterm01,0
[APP-RX] T,yaglabterm01,0
```

## Slotted Beacon Daemon

Run:

```bash
cd /opt/lora-daemon
source .venv/bin/activate
python slotted_beacon_daemon.py
```

The daemon:

```text
- always listens for LoRa packets
- selects one slot per frame
- transmits its own node status once per frame
- encodes position as official BST-ID
- stores received node status into Redis
```

Current beacon format:

```text
B,<node2>,<seq>,<bit_len>,<bst_hex>,<alt>,<spd10>,<heading>
```

Example:

```text
B,02,11,46,31EFE383632C,256,3,185
```

This does not include raw latitude/longitude.
Web-side node display recovers approximate tile center from BST-ID.

## Redis Keys

Received raw lines:

```text
lora:raw:rx
```

Parsed node states:

```text
lora:nodes:<node_id>
```

Radio state:

```text
lora:state:radio
```

Events:

```text
lora:event
```

Examples:

```bash
redis-cli LRANGE lora:raw:rx 0 -1
redis-cli KEYS 'lora:nodes:*'
redis-cli GET lora:nodes:yaglabterm02 | python3 -m json.tool
```

## Redis Cleanup

The daemon may clean old LoRa keys at startup.

Typical patterns:

```text
lora:raw:*
lora:event
lora:state:radio
lora:nodes:*
```

GPS Redis keys such as `state:gps` and `state:self` should not be deleted by LoRa daemon.

## systemd Service

Create:

```bash
sudo nano /etc/systemd/system/lora-daemon.service
```

Example:

```ini
[Unit]
Description=LoRa EASEL Slotted Beacon Daemon
After=network.target redis-server.service
Wants=redis-server.service

[Service]
WorkingDirectory=/opt/lora-daemon
ExecStart=/opt/lora-daemon/.venv/bin/python /opt/lora-daemon/slotted_beacon_daemon.py
Restart=always
RestartSec=3
User=YOUR_USERNAME
Group=dialout
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lora-daemon.service
```

Logs:

```bash
journalctl -u lora-daemon.service -f
```

## Development Roadmap

### Current

```text
- ES920LR Python TX/RX
- 500 kHz / SF8 / channel 1
- Slotted ALOHA-like self beacon
- BST-ID hex beacon
- Redis node state
```

### Next

```text
- Packet / Token layer
- Flooding
- Duplicate suppression
- TTL / hop count
- BST-ID containment and directional forwarding
- Implicit ACK based on returned packet similarity
- Business/Web driver interface
```

## Design Notes

ACK/ORDER/ASK/REPLY are not implemented directly in the raw beacon layer.

The planned division is:

```text
Business/Web Driver:
  understands business protocol and SQLite

LoRa Daemon:
  understands radio, mesh propagation, duplicate suppression, BST-ID routing

Redis:
  boundary and runtime state
```

## Related Repositories

```text
lora-gateway-web
  Web UI, business process, SQLite, node/event maps

gps-redis-service
  GPS/gpsd/chrony state to Redis

bst-id
  BST-ID Python library
```
