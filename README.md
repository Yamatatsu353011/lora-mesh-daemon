# lora-mesh-daemon

LoRa/BST-IDを利用した分散型通信システムです。

EASEL ES920LRをPythonから制御し、LoRa送受信、GPS/BST-IDによる位置共有、Slotted ALOHA、ASK/REPLY通信、パケット中継などを行います。

Web側の発注・受注処理とはRedisを介して連携します。

## 1. 開発環境

**ハードウェア**
- Ubuntu搭載PC / Windows PC / Raspberry Pi
- EASEL ES920LR
- GPS/GNSS受信機（使用するノードのみ）

**ソフトウェア**
- Python 3
- Redis
- Git
- Python仮想環境（venv）

## 2. 主なファイル

| ファイル | 役割 |
|---|---|
| `config.py` | ノードID・通信設定 |
| `easel_radio.py` | LoRaデバイス制御 |
| `raw_daemon.py` | LoRa送受信・Redis連携 |
| `mesh_daemon.py` | ASK/REPLY生成・イベント処理 |
| `routing.py` | パケット中継・重複排除・経路制御 |
| `packet.py` | パケット構造・エンコード |
| `slotted_aloha.py` | 送信タイミング制御 |
| `gps_redis_service.py` | GPS情報をRedisへ保存 |
| `ask_sender.py` | ASK送信処理 |
| `rx_test.py` / `tx_test.py` | 送受信テスト |

## 3. 環境構築

以下はUbuntu環境の例です。

### リポジトリの取得

```bash
cd /opt
sudo git clone https://github.com/Yamatatsu353011/lora-mesh-daemon.git
sudo chown -R $USER:$USER /opt/lora-mesh-daemon
```

### Python仮想環境

```bash
cd /opt/lora-mesh-daemon

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install pyserial redis python-dotenv pynmea2
```

### BST-IDライブラリ

```bash
cd /opt

sudo git clone https://github.com/yagu1/bst-id.git
sudo chown -R $USER:$USER /opt/bst-id

cd /opt/lora-mesh-daemon
source .venv/bin/activate

pip install -e /opt/bst-id
```

確認：

```bash
python -c "from bst_id.encoder import BSTIDEncoder; print('BST-ID OK')"
```

## 4. config.pyの設定

GitHubには設定テンプレートとして`config_temolate.py`が用意されています。

```bash
cd /opt/lora-mesh-daemon

cp config_temolate.py config.py
nano config.py
```

### ノードID

基地局1：

```python
NODE_ID = "yaglabterm01"
OWN_ID = "0001"
```

基地局2：

```python
NODE_ID = "yaglabterm02"
OWN_ID = "0002"
```

`OWN_ID`はノードごとに異なる値を設定してください。

### GPS設定

GPSを使用しない基地局：

```python
USE_DUMMY_GPS = True

DUMMY_LAT = 37.521844
DUMMY_LON = 139.939698
```

GPSを使用するノード：

```python
USE_DUMMY_GPS = False
GPS_SERIAL_PORT = "/dev/ttyS0"
GPS_BAUDRATE = 9600
```

GPSのポートは実際の接続環境に合わせて変更してください。

### LoRa通信設定

```python
SERIAL_PORT = "/dev/lora0"
BAUDRATE = 115200

BW = "6"
SF = "8"
CH = "1"

PAN_ID = "ABCD"
DST_ID = "FFFF"

ACK = "2"
RETRY = "0"

TRANSMODE = "1"
FORMAT = "2"
```

| 項目 | 設定 |
|---|---|
| BW | 500 kHz |
| SF | 8 |
| CH | 1 |
| PAN_ID | ABCD |
| DST_ID | FFFF（ブロードキャスト） |
| FORMAT | 2（バイナリ形式） |

通信するノード間でBW・SF・CH・PAN_IDを一致させてください。

Windowsでは`SERIAL_PORT = "COM3"`など、実際のポート名を指定します。

### Redis設定

```python
REDIS_ENABLED = True

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0
```

Redisを別のPCや環境で動かす場合は、接続先を適切に変更してください。

また、テンプレートには次の設定があります。

```python
REDIS_CLEAN_ON_START = True
```

起動時に既存のLoRa関連Redisデータを削除する設定です。既存データを保持したい場合は`False`に変更してください。

## 5. 動作確認

### Redis接続確認

```bash
redis-cli ping
```

正常時：

```text
PONG
```

### LoRa送受信テスト

受信側：

```bash
python rx_test.py
```

送信側：

```bash
python tx_test.py
```

※ 同じLoRaデバイスを複数のプログラムから同時に操作しないでください。

## 6. デーモンの起動

各プログラムは別のターミナルで起動します。

### GPS情報取得

```bash
python gps_redis_service.py
```

### LoRa送受信

```bash
python raw_daemon.py
```

### メッシュ通信処理

```bash
python mesh_daemon.py
```

GPS情報取得 → LoRa送受信 → メッシュ通信処理の順に起動すると、動作を確認しやすくなります。

※ systemdでサービス化している場合は、同じプログラムを重複起動しないでください。

## 7. Redisデータ確認

主なキー：

| キー | 内容 |
|---|---|
| `state:self` | 自ノードの位置 |
| `lora:raw:tx` | LoRa送信キュー |
| `lora:raw:rx` | LoRa受信データ |
| `lora:nodes:*` | 他ノードの位置情報 |
| `lora:event` | LoRa関連イベント |

確認例：

```bash
redis-cli GET state:self
redis-cli LRANGE lora:raw:rx 0 -1
redis-cli KEYS 'lora:nodes:*'
```

## 8. 注意事項

- ノードごとに`config.py`を設定すること。
- LoRa通信パラメータは全ノードで統一すること。
- Redisが起動していることを確認すること。
- 無線デバイスを複数のプログラムで同時使用しないこと。
- `config.py`はGit管理対象外のため、各環境で作成すること。
- 実機とGitHubのコード・設定が一致しているか確認すること。

Web側の発注・受注画面については、別リポジトリ`lora-gateway-web`で管理します。


## Windows環境でのセットアップ

WindowsではPowerShellを使用して環境構築します。

LoRa制御プログラムはWindows上で実行し、RedisはWSL（Ubuntu）側で動作させます。

### 1. Python・Gitのインストール

PowerShellで実行します。

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
```

インストール後、PowerShellを開き直して確認します。

```powershell
python --version
git --version
```

### 2. リポジトリの取得

```powershell
cd $HOME\Documents

New-Item -ItemType Directory -Force LoRa
cd LoRa

git clone https://github.com/Yamatatsu353011/lora-mesh-daemon.git
```

### 3. Python仮想環境

```powershell
cd lora-mesh-daemon

python -m venv .venv

.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install pyserial redis python-dotenv pynmea2
```

仮想環境を有効化できない場合は、PowerShellで以下を実行してから再試行します。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 4. BST-IDライブラリ

`lora-mesh-daemon`と同じ階層にインストールします。

```powershell
cd ..

git clone https://github.com/yagu1/bst-id.git

cd lora-mesh-daemon

python -m pip install -e ..\bst-id
```

動作確認：

```powershell
python -c "from bst_id.encoder import BSTIDEncoder; print('BST-ID OK')"
```

### 5. config.pyの設定

```powershell
Copy-Item .\config_temolate.py .\config.py

notepad .\config.py
```

Windowsの場合、シリアルポートをCOMポートに変更します。

```python
SERIAL_PORT = "COM3"
BAUDRATE = 115200
```

基地局ごとにノードIDを変更します。

基地局1：

```python
NODE_ID = "yaglabterm01"
OWN_ID = "0001"
```

基地局2：

```python
NODE_ID = "yaglabterm02"
OWN_ID = "0002"
```

GPSを使用しない基地局では、ダミーGPSを使用します。

```python
USE_DUMMY_GPS = True
```

Redis接続設定：

```python
REDIS_ENABLED = True
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0
```

RedisはWSL側で起動します。

Windowsから`127.0.0.1`で接続できない場合は、WSLとのネットワーク設定を確認してください。

### 6. Redis接続確認

WSL側でRedisを起動した状態で、WindowsのPowerShellから実行します。

```powershell
Test-NetConnection 127.0.0.1 -Port 6379
```

Pythonからの接続確認：

```powershell
python -c "import redis; print(redis.Redis(host='127.0.0.1', port=6379).ping())"
```

正常時は`True`が表示されます。

### 7. プログラム起動

PowerShellを2つ開きます。

**PowerShell 1：LoRa送受信**

```powershell
cd $HOME\Documents\LoRa\lora-mesh-daemon

.\.venv\Scripts\python.exe raw_daemon.py
```

**PowerShell 2：メッシュ通信**

```powershell
cd $HOME\Documents\LoRa\lora-mesh-daemon

.\.venv\Scripts\python.exe mesh_daemon.py
```

### 注意事項

- Windowsでは`sudo apt`を使用しません。
- PythonライブラリはWindows側の仮想環境にインストールします。
- シリアルポートは`COM3`など、実際に接続されているポートを指定します。
- RedisはWSL側で起動します。
- `raw_daemon.py`と`mesh_daemon.py`は別のターミナルで実行します。
