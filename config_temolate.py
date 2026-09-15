# -*- coding: utf-8 -*-

# ============================================================
# Node identity
# ============================================================

# Change for each node
# Example:
#   yaglabterm01 -> NODE_ID = "yaglabterm01", OWN_ID = "0001"
#   yaglabterm02 -> NODE_ID = "yaglabterm02", OWN_ID = "0002"
#   yaglabterm03 -> NODE_ID = "yaglabterm03", OWN_ID = "0003"

NODE_ID = "yaglabterm02"

# EASEL own node ID
OWN_ID = "0002"

# Data held by this node
MY_DATA_IDS = {"a", "b", "c"}

# ============================================================
# Position source
# ============================================================

# GPSがないノードはTrue
USE_DUMMY_GPS = True

# 仮座標
DUMMY_LAT = 37.521844
DUMMY_LON = 139.939698


# ============================================================
# Serial port
# ============================================================

# Raspberry Pi / Ubuntu
SERIAL_PORT = "/dev/lora0"

BAUDRATE = 115200
SERIAL_TIMEOUT_SEC = 0.5


# ============================================================
# EASEL ES920LR radio settings
# ============================================================
#
# bw:
#   3 = 62.5 kHz
#   4 = 125 kHz
#   5 = 250 kHz
#   6 = 500 kHz
#
# sf:
#   7 - 12
#
# channel:
#   62.5/125 kHz : 1 - 15
#   250 kHz      : 1 - 7
#   500 kHz      : 1 - 5
#
# Current setting:
#   500 kHz
#   SF8
#   Channel 1
#   Payload mode
#   Binary mode
# ============================================================

BW = "6"
SF = "8"
CH = "1"

PAN_ID = "ABCD"
DST_ID = "FFFF"      # Broadcast

ACK = "2"            # 1: ON, 2: OFF
RETRY = "0"

TRANSMODE = "1"      # 1: Payload, 2: Frame
FORMAT = "2"         # 1: ASCII, 2: BINARY

RCVID = "2"          # 1: ON, 2: OFF
RSSI = "2"           # 1: ON, 2: OFF

POWER = "13"         # -4 to 13 dBm


# ============================================================
# Commands sent to ES920LR before start
# ============================================================

EASEL_CONFIG_COMMANDS = [
    ("bw", BW),
    ("sf", SF),
    ("channel", CH),
    ("panid", PAN_ID),
    ("ownid", OWN_ID),
    ("dstid", DST_ID),
    ("ack", ACK),
    ("retry", RETRY),
    ("transmode", TRANSMODE),
    ("format", FORMAT),
    ("rcvid", RCVID),
    ("rssi", RSSI),
    ("power", POWER),
]


# ============================================================
# Redis
# ============================================================

REDIS_ENABLED = True

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0


# ------------------------------------------------------------
# LoRa raw TX / RX
# ------------------------------------------------------------

REDIS_RAW_TX = "lora:raw:tx"
REDIS_RAW_RX = "lora:raw:rx"


# ------------------------------------------------------------
# LoRa event channel
# ------------------------------------------------------------

REDIS_EVENT = "lora:event"


# ------------------------------------------------------------
# Radio state
# ------------------------------------------------------------

REDIS_STATE = "lora:state:radio"


# ------------------------------------------------------------
# Own GPS position
#
# gps-redis-service etc. writes own GPS information here.
# mesh/routing reads this and generates BST-ID.
# ------------------------------------------------------------

REDIS_GPS_STATE_KEY = "state:self"


# ------------------------------------------------------------
# Other node positions
#
# Example:
#   lora:nodes:0001
#   lora:nodes:0002
#   lora:nodes:0003
# ------------------------------------------------------------

REDIS_NODE_PREFIX = "lora:nodes:"

# Node-position expiration time
REDIS_NODE_TTL_SEC = 60


# ============================================================
# BST-ID settings
# ============================================================

# Current system uses X/Y only.
#
# X = longitude
# Y = latitude
#
# Zoom 32 is used for the current GPS/BST-ID implementation.

BST_ZX = 32
BST_ZY = 32

# LoRa position representation
BEACON_FORMAT = "BST_ID"


# ============================================================
# Binary LoRa packet
# ============================================================

# ES920LR binary payload maximum used by the current mesh packet.
MAX_BINARY_PAYLOAD_LEN = 50

MAX_TX_LINE_LEN = 80


# ============================================================
# Slotted ALOHA / self-position beacon
# ============================================================

SLOTTED_ENABLED = True

# One frame every 5 seconds
FRAME_SEC = 5.0

# 0.5 sec slot x 10 slots
SLOT_SEC = 0.5

# Avoid slot boundary
TX_GUARD_SEC = 0.05

# Send own position once per frame
SELF_BEACON_ENABLED = True


# ============================================================
# Redis cleanup on daemon startup
# ============================================================

REDIS_CLEAN_ON_START = True

REDIS_CLEAN_PATTERNS = [
    "lora:raw:*",
    "lora:event",
    "lora:state:radio",
    "lora:nodes:*",
]
