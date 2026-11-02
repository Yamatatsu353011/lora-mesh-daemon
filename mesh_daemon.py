# -*- coding: utf-8 -*-

import json
import math
import time

import redis

import config

from packet import (
    Packet,
    make_ask_packet,
    make_reply_packet,
)

from bst_id.encoder import BSTIDEncoder
from bst_id.decoder import BSTIDDecoder

from routing import Routing


# ============================================================
# Redis channels
# ============================================================

ORDER_CHANNEL = "event:business:order_created"
OFFER_CHANNEL = "event:business:offer_created"

# NEW: 物資受領確認
RECEIPT_CHANNEL = "event:business:receipt_created"


COUNTER_KEY = f"lora:order:ask_seq:{config.OWN_ID}"

TTL = 10


# ============================================================
# Item definitions
# ============================================================

ITEM_CODES = (
    "water",
    "hardtack",
    "rice_meal",
    "other_food",
    "supplement",
    "cold_medicine",
    "painkiller",
    "stomach_medicine",
    "eye_drops",
    "bandage",
    "compress",
    "blanket",
    "mat",
    "cushion",
    "portable_toilet",
    "battery",
    "light",
)

ITEM_TO_NUM = {
    code: i + 1
    for i, code in enumerate(ITEM_CODES)
}


# ============================================================
# Distance
# ============================================================

def haversine_distance_m(
    lat1,
    lon1,
    lat2,
    lon2,
):
    radius = 6371000.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlambda / 2) ** 2
    )

    return (
        2
        * radius
        * math.atan2(
            math.sqrt(a),
            math.sqrt(max(0.0, 1 - a)),
        )
    )


# ============================================================
# Base36
# ============================================================

def base36(n):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    if n <= 0:
        return "0"

    out = ""

    while n:
        n, remain = divmod(n, 36)
        out = chars[remain] + out

    return out


# ============================================================
# BST-ID
# ============================================================

def encode_position(
    lon,
    lat,
    zx,
    zy,
):
    return BSTIDEncoder.encode(
        lon,
        lat,
        None,
        None,
        int(zx),
        int(zy),
        0,
        0,
    )


def own_position(r):
    raw = r.get(config.REDIS_GPS_STATE_KEY)

    if raw is None:
        raise ValueError(
            "位置情報がありません: "
            f"{config.REDIS_GPS_STATE_KEY}"
        )

    state = json.loads(raw)

    return (
        float(state["lat"]),
        float(state["lon"]),
    )


def own_bst_position(r):
    """
    自ノードの現在位置をBST-IDに変換する。
    ASK / REPLY / 受領確認で共通利用。
    """

    lat, lon = own_position(r)

    zx = getattr(
        config,
        "BST_ZOOM_X",
        getattr(config, "BST_ZX", None),
    )

    zy = getattr(
        config,
        "BST_ZOOM_Y",
        getattr(config, "BST_ZY", None),
    )

    if zx is None or zy is None:
        raise ValueError(
            "BST zoom setting missing"
        )

    bst, bits = encode_position(
        lon,
        lat,
        zx,
        zy,
    )

    return lat, lon, bst, bits


def goal_from_event(msg):
    target = msg.get("target") or {}

    zx = target.get("zx")
    zy = target.get("zy")

    if zx is None or zy is None:
        raise ValueError(
            "目標ズーム zx / zy がありません"
        )

    zx = int(zx)
    zy = int(zy)

    if not (
        0 <= zx <= 30
        and 0 <= zy <= 30
    ):
        raise ValueError(
            f"不正なズーム zx={zx} zy={zy}"
        )

    bounds = target.get("bounds") or {}

    if all(
        bounds.get(k) is not None
        for k in (
            "south",
            "north",
            "west",
            "east",
        )
    ):
        lat = (
            float(bounds["south"])
            + float(bounds["north"])
        ) / 2.0

        lon = (
            float(bounds["west"])
            + float(bounds["east"])
        ) / 2.0

    else:
        point = (
            target.get("clicked_point")
            or {}
        )

        lat = float(point["lat"])
        lon = float(point["lon"])

    if not (
        -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        raise ValueError(
            "目標座標が範囲外です"
        )

    return lat, lon, zx, zy


# ============================================================
# ASK: Web発注
# ============================================================

def process_order(r, routing, msg):

    if msg.get("type") != "ORDER_REQUEST":
        return

    order_id = msg.get(
        "order_id",
        "(unknown)",
    )

    items = msg.get("items") or []

    if not items:
        print(
            f"[ORDER-SKIP] "
            f"order={order_id} "
            f"物資がありません",
            flush=True,
        )
        return

    # --------------------------------------------------------
    # 自分の位置
    # --------------------------------------------------------

    lat, lon, start_bst, start_bits = (
        own_bst_position(r)
    )

    # --------------------------------------------------------
    # 発注目標位置
    # --------------------------------------------------------

    goal_lat, goal_lon, zx, zy = (
        goal_from_event(msg)
    )

    goal_bst, goal_bits = encode_position(
        goal_lon,
        goal_lat,
        zx,
        zy,
    )

    distance = haversine_distance_m(
        lat,
        lon,
        goal_lat,
        goal_lon,
    )

    # --------------------------------------------------------
    # 物資ごとにASKを生成
    # --------------------------------------------------------

    for item in items:

        code = item.get("item_code")

        if code not in ITEM_TO_NUM:
            print(
                f"[ORDER-SKIP] "
                f"未対応 item_code={code}",
                flush=True,
            )
            continue

        try:
            qty = int(
                item.get("requested_qty")
            )

        except (TypeError, ValueError):
            print(
                f"[ORDER-SKIP] "
                f"数量が不正 item_code={code}",
                flush=True,
            )
            continue

        if qty <= 0 or qty > 9999:
            print(
                f"[ORDER-SKIP] "
                f"数量範囲外 "
                f"item_code={code} qty={qty}",
                flush=True,
            )
            continue

        # O01:3 = 水3本を発注
        data_id = (
            f"O{ITEM_TO_NUM[code]:02d}:{qty}"
        )

        seq = r.incr(COUNTER_KEY)

        pkt_id = (
            f"{config.OWN_ID}-{base36(seq)}"
        )

        packet = make_ask_packet(
            pkt_id=pkt_id,
            goal_bst=goal_bst,
            goal_bit_len=goal_bits,
            start_bst=start_bst,
            start_bit_len=start_bits,
            data_id=data_id,
            ttl=TTL,
            distance=distance,
        )

        payload = packet.encode()

        max_bytes = int(
            getattr(
                config,
                "MAX_BINARY_PAYLOAD_LEN",
                50,
            )
        )

        if len(payload) > min(50, max_bytes):
            print(
                f"[ORDER-DROP] "
                f"packet too large "
                f"order={order_id} "
                f"size={len(payload)}",
                flush=True,
            )
            continue

        # 元の発注情報をRedisに保存
        mapping = {
            "pkt_id": pkt_id,
            "order_id": order_id,
            "origin_node": str(
                config.OWN_ID
            ),
            "item_code": code,
            "item_no": ITEM_TO_NUM[code],
            "requested_qty": qty,
            "created_at": msg.get(
                "created_at"
            ),
        }

        # 保存とキュー登録をまとめて実行
        with r.pipeline(
            transaction=True
        ) as tx:

            tx.set(
                f"lora:orders:outgoing:{pkt_id}",
                json.dumps(mapping),
            )

            tx.lpush(
                config.REDIS_RAW_TX,
                payload.hex(),
            )

            tx.execute()

        routing.mark_sent(packet)

        print(
            f"[ORDER-ASK-QUEUED] "
            f"order={order_id} "
            f"pkt={pkt_id} "
            f"item={code} "
            f"qty={qty} "
            f"data_id={data_id} "
            f"goal=({goal_lat:.7f},"
            f"{goal_lon:.7f}) "
            f"ttl={TTL} "
            f"bytes={len(payload)}",
            flush=True,
        )


# ============================================================
# REPLY(P): 提供可能数の回答
# ============================================================

def process_offer(r, routing, msg):

    if msg.get("type") != "ORDER_REPLY":
        return

    ask_pkt_id = msg.get("ask_pkt_id")

    if (
        not isinstance(ask_pkt_id, str)
        or "-" not in ask_pkt_id
    ):
        print(
            f"[ORDER-REPLY-SKIP] "
            f"invalid ASK ID: {ask_pkt_id!r}",
            flush=True,
        )
        return

    origin = ask_pkt_id.split(
        "-",
        1,
    )[0]

    if origin == str(config.OWN_ID):
        print(
            f"[ORDER-REPLY-SKIP] "
            f"own ASK: {ask_pkt_id}",
            flush=True,
        )
        return

    # --------------------------------------------------------
    # 受信済みASKを確認
    # --------------------------------------------------------

    original_raw = r.get(
        f"lora:orders:received:"
        f"{origin}:{ask_pkt_id}"
    )

    if not original_raw:
        print(
            f"[ORDER-REPLY-SKIP] "
            f"unknown received ASK: "
            f"{ask_pkt_id}",
            flush=True,
        )
        return

    original = json.loads(
        original_raw
    )

    if str(
        original.get("received_by")
    ) != str(config.OWN_ID):
        raise ValueError(
            "ASK was not received by this BASE"
        )

    if (
        msg.get("order_id")
        != f"lora-{ask_pkt_id}"
    ):
        raise ValueError(
            "Web order_id/ASK pkt_id mismatch"
        )

    items = msg.get("items") or []

    if len(items) != 1:
        raise ValueError(
            "expected 1 reply item "
            f"per LoRa ASK, got {len(items)}"
        )

    item = items[0]

    item_code = original.get(
        "item_code"
    )

    if (
        item.get("item_code")
        != item_code
    ):
        raise ValueError(
            "REPLY item does not match "
            "received ASK"
        )

    if item_code not in ITEM_TO_NUM:
        raise ValueError(
            f"unsupported item_code={item_code}"
        )

    qty = int(
        item.get("accepted_qty")
    )

    requested = int(
        original["requested_qty"]
    )

    if not (
        0 <= qty <= requested <= 9999
    ):
        raise ValueError(
            f"invalid accepted_qty={qty}, "
            f"requested={requested}"
        )

    # --------------------------------------------------------
    # 送信元BST-ID
    # --------------------------------------------------------

    lat, lon, start_bst, start_bits = (
        own_bst_position(r)
    )

    # --------------------------------------------------------
    # 返信先は発注元
    # --------------------------------------------------------

    goal_bst = int(
        original["start_bst"]
    )

    goal_bits = int(
        original["start_bit_len"]
    )

    goal_lon, goal_lat, _, _ = (
        BSTIDDecoder.decode(
            goal_bst,
            goal_bits,
        )
    )

    distance = haversine_distance_m(
        lat,
        lon,
        goal_lat,
        goal_lon,
    )

    if distance <= 0:
        distance = 0.01

    # --------------------------------------------------------
    # REPLYパケット生成
    # --------------------------------------------------------

    responder36 = base36(
        int(str(config.OWN_ID))
    )

    # P01:2:2 = 基地局0002が水2本を提供可能
    data_id = (
        f"P{ITEM_TO_NUM[item_code]:02d}:"
        f"{qty}:"
        f"{responder36}"
    )

    packet = make_reply_packet(
        pkt_id=ask_pkt_id,
        goal_bst=goal_bst,
        goal_bit_len=goal_bits,
        start_bst=start_bst,
        start_bit_len=start_bits,
        data_id=data_id,
        ttl=TTL,
        distance=distance,
    )

    payload = packet.encode()

    max_bytes = min(
        50,
        int(
            getattr(
                config,
                "MAX_BINARY_PAYLOAD_LEN",
                50,
            )
        ),
    )

    if len(payload) > max_bytes:
        raise ValueError(
            "REPLY exceeds radio payload limit: "
            f"{len(payload)}"
        )

    # --------------------------------------------------------
    # 二重送信防止
    # --------------------------------------------------------

    guard = (
        f"lora:orders:reply_tx:{ask_pkt_id}"
    )

    if not r.set(
        guard,
        str(msg.get("offer_id", "")),
        nx=True,
    ):
        print(
            f"[ORDER-REPLY-DUP] "
            f"ask={ask_pkt_id} "
            f"already sent",
            flush=True,
        )
        return

    try:
        r.lpush(
            config.REDIS_RAW_TX,
            payload.hex(),
        )

    except Exception:
        r.delete(guard)
        raise

    routing.mark_sent(packet)

    print(
        f"[ORDER-REPLY-QUEUED] "
        f"ask={ask_pkt_id} "
        f"item={item_code} "
        f"accepted={qty} "
        f"bytes={len(payload)} "
        f"responder={config.OWN_ID}",
        flush=True,
    )


# ============================================================
# NEW: REPLY(C) 物資受領確認
# ============================================================

def process_receipt(r, routing, msg):
    """
    発注元BASEから受注元BASEへ受領確認を通知する。

    REPLYの既存バイナリ形式を流用する。

    P01:2:2
        物資01について0002が2個提供可能

    C01:2:1
        物資01について0001が2個受領済み

    pkt_idは元のASK IDを使用する。
    """

    if msg.get("type") != "ORDER_RECEIPT":
        return

    # --------------------------------------------------------
    # ASK ID
    # --------------------------------------------------------

    ask_pkt_id = msg.get(
        "ask_pkt_id"
    )

    if (
        not isinstance(ask_pkt_id, str)
        or "-" not in ask_pkt_id
    ):
        raise ValueError(
            "invalid ask_pkt_id"
        )

    origin = ask_pkt_id.split(
        "-",
        1,
    )[0]

    # 受領確認は元の発注元が送信する
    if origin != str(config.OWN_ID):
        raise ValueError(
            "receipt must be sent by requester"
        )

    # --------------------------------------------------------
    # 元のASKとREPLYを取得
    # --------------------------------------------------------

    original_raw = r.get(
        f"lora:orders:outgoing:{ask_pkt_id}"
    )

    reply_raw = r.get(
        f"lora:orders:reply_received:{ask_pkt_id}"
    )

    if not original_raw:
        raise ValueError(
            "original ASK not found"
        )

    if not reply_raw:
        raise ValueError(
            "received REPLY not found"
        )

    original = json.loads(
        original_raw
    )

    reply = json.loads(
        reply_raw
    )

    # --------------------------------------------------------
    # Web発注IDの照合
    # --------------------------------------------------------

    order_id = msg.get("order_id")

    if str(order_id) != str(
        original["order_id"]
    ):
        raise ValueError(
            "order_id mismatch"
        )

    # --------------------------------------------------------
    # 数量の照合
    # --------------------------------------------------------

    item_no = int(
        original["item_no"]
    )

    requested_qty = int(
        original["requested_qty"]
    )

    accepted_qty = int(
        reply["accepted_qty"]
    )

    received_qty = int(
        msg.get("received_qty")
    )

    if not (
        0 <= received_qty
        <= accepted_qty
        <= requested_qty
    ):
        raise ValueError(
            "invalid received quantity"
        )

    # 初期実装は一度に受領確定する
    if received_qty != accepted_qty:
        raise ValueError(
            "partial receipt "
            "is not supported yet"
        )

    # --------------------------------------------------------
    # 回答元基地局の確認
    # --------------------------------------------------------

    responder = str(
        reply["responder_node"]
    )

    if str(
        msg.get("responder_node")
    ) != responder:
        raise ValueError(
            "responder mismatch"
        )

    # --------------------------------------------------------
    # 自ノードの位置
    # --------------------------------------------------------

    lat, lon, start_bst, start_bits = (
        own_bst_position(r)
    )

    # --------------------------------------------------------
    # 受注元の位置
    #
    # routing.pyでP-REPLY受信時に保存する。
    # --------------------------------------------------------

    target_raw = r.get(
        f"lora:orders:reply_position:{ask_pkt_id}"
    )

    if not target_raw:
        raise ValueError(
            "responder position not found"
        )

    target = json.loads(
        target_raw
    )

    goal_bst = int(
        target["start_bst"]
    )

    goal_bits = int(
        target["start_bit_len"]
    )

    goal_lon, goal_lat, _, _ = (
        BSTIDDecoder.decode(
            goal_bst,
            goal_bits,
        )
    )

    distance = haversine_distance_m(
        lat,
        lon,
        goal_lat,
        goal_lon,
    )

    if distance <= 0:
        distance = 0.01

    # --------------------------------------------------------
    # 受領確認パケット
    #
    # msg_type = REPLY
    # data_id  = C01:2:1
    # --------------------------------------------------------

    origin36 = base36(
        int(str(config.OWN_ID))
    )

    data_id = (
        f"C{item_no:02d}:"
        f"{received_qty}:"
        f"{origin36}"
    )

    packet = make_reply_packet(
        pkt_id=ask_pkt_id,
        goal_bst=goal_bst,
        goal_bit_len=goal_bits,
        start_bst=start_bst,
        start_bit_len=start_bits,
        data_id=data_id,
        ttl=TTL,
        distance=distance,
    )

    payload = packet.encode()

    max_bytes = min(
        50,
        int(
            getattr(
                config,
                "MAX_BINARY_PAYLOAD_LEN",
                50,
            )
        ),
    )

    if len(payload) > max_bytes:
        raise ValueError(
            "receipt packet too large: "
            f"{len(payload)}"
        )

    # --------------------------------------------------------
    # 二重送信防止
    #
    # P用のreply_txとは別のキーを使う。
    # --------------------------------------------------------

    guard_key = (
        f"lora:orders:receipt_tx:{ask_pkt_id}"
    )

    receipt_record = {
        "type": "ORDER_RECEIPT",
        "order_id": order_id,
        "ask_pkt_id": ask_pkt_id,
        "item_no": item_no,
        "received_qty": received_qty,
        "responder_node": responder,
        "sender_node": str(
            config.OWN_ID
        ),
        "created_at": time.time(),
    }

    # Redisのトランザクションで
    # 二重登録防止と送信キュー登録を行う。
    #
    # すでにguard_keyがある場合は登録しない。
    with r.pipeline() as tx:
        while True:
            try:
                tx.watch(guard_key)

                if tx.exists(guard_key):
                    tx.unwatch()

                    print(
                        f"[ORDER-RECEIPT-DUP] "
                        f"ask={ask_pkt_id}",
                        flush=True,
                    )

                    return

                tx.multi()

                tx.set(
                    guard_key,
                    json.dumps(receipt_record),
                )

                tx.lpush(
                    config.REDIS_RAW_TX,
                    payload.hex(),
                )

                tx.execute()
                break

            except redis.WatchError:
                continue

    routing.mark_sent(packet)

    print(
        f"[ORDER-RECEIPT-QUEUED] "
        f"ask={ask_pkt_id} "
        f"item={item_no} "
        f"received={received_qty} "
        f"to={responder} "
        f"data_id={data_id} "
        f"bytes={len(payload)}",
        flush=True,
    )


# ============================================================
# HEX -> Packet
# ============================================================

def decode_payload_hex(payload_hex: str):
    """
    Redis上のHEX文字列を
    Packetオブジェクトに変換する。
    """

    if not isinstance(
        payload_hex,
        str,
    ):
        return None

    try:
        payload = bytes.fromhex(
            payload_hex
        )

    except ValueError:
        print(
            f"[MESH] invalid HEX: "
            f"{payload_hex}",
            flush=True,
        )
        return None

    packet = Packet.decode(
        payload
    )

    if packet is None:
        print(
            f"[MESH] invalid packet "
            f"len={len(payload)} "
            f"hex={payload_hex}",
            flush=True,
        )
        return None

    return packet


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # Redis接続
    # --------------------------------------------------------

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    r.ping()

    # --------------------------------------------------------
    # Dummy GPS
    # --------------------------------------------------------

    if getattr(
        config,
        "USE_DUMMY_GPS",
        False,
    ):

        dummy_position = {
            "lat": config.DUMMY_LAT,
            "lon": config.DUMMY_LON,
            "source": "dummy",
        }

        r.set(
            config.REDIS_GPS_STATE_KEY,
            json.dumps(
                dummy_position
            ),
        )

        print(
            f"[DUMMY-GPS] "
            f"lat={config.DUMMY_LAT} "
            f"lon={config.DUMMY_LON}",
            flush=True,
        )

    # --------------------------------------------------------
    # Routing
    # --------------------------------------------------------

    routing = Routing(r)

    # --------------------------------------------------------
    # Redis Pub/Sub
    # --------------------------------------------------------

    pubsub = r.pubsub(
        ignore_subscribe_messages=True
    )

    channels = [
        config.REDIS_EVENT
    ]

    # 基地局のみWebイベントを購読
    if getattr(
        config,
        "NODE_ROLE",
        None,
    ) == "BASE":

        channels.extend(
            (
                ORDER_CHANNEL,
                OFFER_CHANNEL,
                RECEIPT_CHANNEL,
            )
        )

    pubsub.subscribe(
        *channels
    )

    print(
        f"[MESH-CHANNELS] "
        f"{channels}",
        flush=True,
    )

    print(
        "[MESH-DAEMON] started",
        flush=True,
    )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    try:
        while True:

            message = pubsub.get_message(
                timeout=1.0
            )

            if message is None:
                time.sleep(0.05)
                continue

            # ------------------------------------------------
            # JSONイベントを取得
            # ------------------------------------------------

            try:
                event = json.loads(
                    message["data"]
                )

            except (
                TypeError,
                json.JSONDecodeError,
            ):
                print(
                    f"[MESH] invalid event: "
                    f"{message.get('data')}",
                    flush=True,
                )
                continue

            if not isinstance(
                event,
                dict,
            ):
                continue

            channel = message.get(
                "channel"
            )

            # ------------------------------------------------
            # Web: ASK
            # ------------------------------------------------

            if channel == ORDER_CHANNEL:

                try:
                    process_order(
                        r,
                        routing,
                        event,
                    )

                except Exception as exc:
                    print(
                        f"[ORDER-ASK-ERR] "
                        f"{type(exc).__name__}: "
                        f"{exc}",
                        flush=True,
                    )

                continue

            # ------------------------------------------------
            # Web: P-REPLY
            # ------------------------------------------------

            if channel == OFFER_CHANNEL:

                try:
                    process_offer(
                        r,
                        routing,
                        event,
                    )

                except Exception as exc:
                    print(
                        f"[ORDER-REPLY-ERR] "
                        f"{type(exc).__name__}: "
                        f"{exc}",
                        flush=True,
                    )

                continue

            # ------------------------------------------------
            # NEW: Web: C-REPLY（物資受領確認）
            # ------------------------------------------------

            if channel == RECEIPT_CHANNEL:

                try:
                    process_receipt(
                        r,
                        routing,
                        event,
                    )

                except Exception as exc:
                    print(
                        f"[ORDER-RECEIPT-ERR] "
                        f"{type(exc).__name__}: "
                        f"{exc}",
                        flush=True,
                    )

                continue

            # ------------------------------------------------
            # local_packet
            # ------------------------------------------------

            event_type = event.get(
                "event"
            )

            if event_type == "local_packet":

                payload_hex = event.get(
                    "payload_hex"
                )

                packet = decode_payload_hex(
                    payload_hex
                )

                if packet is None:
                    continue

                routing.mark_sent(
                    packet
                )

                print(
                    f"[MESH-LOCAL] "
                    f"type={packet.msg_type} "
                    f"id={packet.pkt_id}",
                    flush=True,
                )

                continue

            # ------------------------------------------------
            # LoRa受信
            # ------------------------------------------------

            if event_type != "rx":
                continue

            payload_hex = event.get(
                "payload_hex"
            )

            packet = decode_payload_hex(
                payload_hex
            )

            if packet is None:
                continue

            # ------------------------------------------------
            # Routing
            # ------------------------------------------------

            try:
                routing.handle_packet(
                    packet
                )

            except Exception as exc:
                print(
                    f"[MESH-ROUTING-ERR] "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    flush=True,
                )

    except KeyboardInterrupt:
        print(
            "\n[MESH-DAEMON] stopped",
            flush=True,
        )

    finally:
        pubsub.close()


if __name__ == "__main__":
    main()
