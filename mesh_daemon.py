# -*- coding: utf-8 -*-

import json
import math
import time

import redis

import config
from packet import Packet, make_ask_packet, make_reply_packet
from bst_id.encoder import BSTIDEncoder
from bst_id.decoder import BSTIDDecoder
from routing import Routing


ORDER_CHANNEL = "event:business:order_created"
OFFER_CHANNEL = "event:business:offer_created"
COUNTER_KEY = f"lora:order:ask_seq:{config.OWN_ID}"
TTL = 10

ITEM_CODES = (
    "water", "hardtack", "rice_meal", "other_food", "supplement",
    "cold_medicine", "painkiller", "stomach_medicine", "eye_drops",
    "bandage", "compress", "blanket", "mat", "cushion",
    "portable_toilet", "battery", "light",
)
ITEM_TO_NUM = {code: i + 1 for i, code in enumerate(ITEM_CODES)}


def haversine_distance_m(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def base36(n):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n <= 0:
        return "0"
    out = ""
    while n:
        n, remain = divmod(n, 36)
        out = chars[remain] + out
    return out


def encode_position(lon, lat, zx, zy):
    return BSTIDEncoder.encode(lon, lat, None, None, int(zx), int(zy), 0, 0)


def own_position(r):
    raw = r.get(config.REDIS_GPS_STATE_KEY)
    if raw is None:
        raise ValueError(f"位置情報がありません: {config.REDIS_GPS_STATE_KEY}")
    state = json.loads(raw)
    return float(state["lat"]), float(state["lon"])


def goal_from_event(msg):
    target = msg.get("target") or {}
    zx = target.get("zx")
    zy = target.get("zy")
    if zx is None or zy is None:
        raise ValueError("目標ズーム zx / zy がありません")
    zx, zy = int(zx), int(zy)
    if not (0 <= zx <= 30 and 0 <= zy <= 30):
        raise ValueError(f"不正なズーム zx={zx} zy={zy}")

    bounds = target.get("bounds") or {}
    if all(bounds.get(k) is not None for k in ("south", "north", "west", "east")):
        lat = (float(bounds["south"]) + float(bounds["north"])) / 2.0
        lon = (float(bounds["west"]) + float(bounds["east"])) / 2.0
    else:
        point = target.get("clicked_point") or {}
        lat, lon = float(point["lat"]), float(point["lon"])

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("目標座標が範囲外です")
    return lat, lon, zx, zy


def process_order(r, routing, msg):
    if msg.get("type") != "ORDER_REQUEST":
        return
    order_id = msg.get("order_id", "(unknown)")
    items = msg.get("items") or []
    if not items:
        print(f"[ORDER-SKIP] order={order_id} 物資がありません", flush=True)
        return

    lat, lon = own_position(r)
    goal_lat, goal_lon, zx, zy = goal_from_event(msg)
    goal_bst, goal_bits = encode_position(goal_lon, goal_lat, zx, zy)
    # 送信元はノード共通のズームを用いる。コードの設定名両方に対応。
    own_zx = getattr(config, "BST_ZOOM_X", getattr(config, "BST_ZX", None))
    own_zy = getattr(config, "BST_ZOOM_Y", getattr(config, "BST_ZY", None))
    if own_zx is None or own_zy is None:
        raise ValueError("configにBST_ZOOM_X/Y（またはBST_ZX/Y）がありません")
    start_bst, start_bits = encode_position(lon, lat, own_zx, own_zy)
    distance = haversine_distance_m(lat, lon, goal_lat, goal_lon)

    for item in items:
        code = item.get("item_code")
        if code not in ITEM_TO_NUM:
            print(f"[ORDER-SKIP] 未対応 item_code={code}", flush=True)
            continue
        try:
            qty = int(item.get("requested_qty"))
        except (TypeError, ValueError):
            print(f"[ORDER-SKIP] 数量が不正 item_code={code}", flush=True)
            continue
        if qty <= 0 or qty > 9999:
            print(f"[ORDER-SKIP] 数量範囲外 item_code={code} qty={qty}", flush=True)
            continue

        # O + 2桁物資ID + : + 数量。例 O01:10 = 水10本。
        # 通常のデータID（aなど）と衝突させない。
        data_id = f"O{ITEM_TO_NUM[code]:02d}:{qty}"
        # Redisの通番で再起動後も衝突を避ける。
        seq = r.incr(COUNTER_KEY)
        pkt_id = f"{config.OWN_ID}-{base36(seq)}"
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
        payload = packet.encode()  # 50Bなどの制約違反時はここでエラー
        max_bytes = getattr(config, "MAX_BINARY_PAYLOAD_LEN", 50)
        if len(payload) > max_bytes:
            print(f"[ORDER-DROP] packet too large order={order_id} size={len(payload)} max={max_bytes}", flush=True)
            continue

        payload_hex = payload.hex()
        # Order-ID correlation retained in the sender's Redis, not on the radio.
        mapping = {
            "pkt_id": pkt_id, "order_id": order_id, "origin_node": str(config.OWN_ID),
            "item_code": code, "item_no": ITEM_TO_NUM[code],
            "requested_qty": qty, "created_at": msg.get("created_at"),
        }
        # Store the mapping BEFORE the radio is allowed to transmit.
        # This is an at-most-once Redis enqueue, not delivery confirmation.
        with r.pipeline(transaction=True) as tx:
            tx.set(f"lora:orders:outgoing:{pkt_id}", json.dumps(mapping))
            tx.lpush(config.REDIS_RAW_TX, payload_hex)
            tx.execute()
        routing.mark_sent(packet)
        print(
            f"[ORDER-ASK-QUEUED] order={order_id} pkt={pkt_id} "
            f"item={code} qty={qty} data_id={data_id} "
            f"goal=({goal_lat:.7f},{goal_lon:.7f}) ttl={TTL} bytes={len(payload)}",
            flush=True,
        )




# REPLY uses the existing binary Packet type (type=1), with data_id
# Pxx:qty:node36 e.g. P01:2:2 = node 0002 offers 2 units of water.
# One reply is supported per ASK packet ID in this initial implementation.
def process_offer(r, routing, msg):
    if msg.get("type") != "ORDER_REPLY":
        return

    ask_pkt_id = msg.get("ask_pkt_id")
    if not isinstance(ask_pkt_id, str) or "-" not in ask_pkt_id:
        print(f"[ORDER-REPLY-SKIP] invalid ASK ID: {ask_pkt_id!r}", flush=True)
        return
    origin = ask_pkt_id.split("-", 1)[0]
    if origin == str(config.OWN_ID):
        print(f"[ORDER-REPLY-SKIP] own ASK: {ask_pkt_id}", flush=True)
        return

    original_raw = r.get(f"lora:orders:received:{origin}:{ask_pkt_id}")
    if not original_raw:
        print(f"[ORDER-REPLY-SKIP] unknown received ASK: {ask_pkt_id}", flush=True)
        return
    original = json.loads(original_raw)
    if str(original.get("received_by")) != str(config.OWN_ID):
        raise ValueError("ASK was not received by this BASE")
    if msg.get("order_id") != f"lora-{ask_pkt_id}":
        raise ValueError("Web order_id/ASK pkt_id mismatch")

    items = msg.get("items") or []
    if len(items) != 1:
        raise ValueError(f"expected 1 reply item per LoRa ASK, got {len(items)}")
    item = items[0]
    item_code = original.get("item_code")
    if item.get("item_code") != item_code:
        raise ValueError("REPLY item does not match received ASK")
    if item_code not in ITEM_TO_NUM:
        raise ValueError(f"unsupported item_code={item_code}")
    qty = int(item.get("accepted_qty"))
    requested = int(original["requested_qty"])
    if not (0 <= qty <= requested <= 9999):
        raise ValueError(f"invalid accepted_qty={qty}, requested={requested}")

    lat, lon = own_position(r)
    own_zx = getattr(config, "BST_ZOOM_X", getattr(config, "BST_ZX", None))
    own_zy = getattr(config, "BST_ZOOM_Y", getattr(config, "BST_ZY", None))
    if own_zx is None or own_zy is None:
        raise ValueError("BST zoom setting missing")
    start_bst, start_bits = encode_position(lon, lat, own_zx, own_zy)
    goal_bst = int(original["start_bst"])
    goal_bits = int(original["start_bit_len"])
    goal_lon, goal_lat, _, _ = BSTIDDecoder.decode(goal_bst, goal_bits)
    distance = haversine_distance_m(lat, lon, goal_lat, goal_lon)
    if distance <= 0:
        # Co-located/dummy base coordinates can still exchange a direct LoRa
        # REPLY. Relays will not forward if directional progress is impossible.
        distance = 0.01

    # One-character station ID for 0001..0009, allowing enough packet space.
    # Larger station IDs are base36, checked against the 50-byte radio limit.
    responder36 = base36(int(str(config.OWN_ID)))
    data_id = f"P{ITEM_TO_NUM[item_code]:02d}:{qty}:{responder36}"
    packet = make_reply_packet(
        pkt_id=ask_pkt_id,
        goal_bst=goal_bst, goal_bit_len=goal_bits,
        start_bst=start_bst, start_bit_len=start_bits,
        data_id=data_id, ttl=TTL, distance=distance,
    )
    payload = packet.encode()
    if len(payload) > min(50, int(getattr(config, "MAX_BINARY_PAYLOAD_LEN", 50))):
        raise ValueError(f"REPLY exceeds radio payload limit: {len(payload)}")

    # Enforce one reply per ASK at BASE B. Note: with Pub/Sub, a crash before
    # enqueue may lose the event; durable delivery is a later enhancement.
    guard = f"lora:orders:reply_tx:{ask_pkt_id}"
    if not r.set(guard, str(msg.get("offer_id", "")), nx=True):
        print(f"[ORDER-REPLY-DUP] ask={ask_pkt_id} already sent", flush=True)
        return
    try:
        r.lpush(config.REDIS_RAW_TX, payload.hex())
    except Exception:
        r.delete(guard)
        raise
    routing.mark_sent(packet)
    print(
        f"[ORDER-REPLY-QUEUED] ask={ask_pkt_id} item={item_code} "
        f"accepted={qty} bytes={len(payload)} responder={config.OWN_ID}",
        flush=True,
    )


def decode_payload_hex(payload_hex: str):
    """
    Redis上のHEX文字列
        ↓
    bytes
        ↓
    Packet
    """

    if not isinstance(payload_hex, str):
        return None

    try:
        payload = bytes.fromhex(payload_hex)

    except ValueError:
        print(
            f"[MESH] invalid HEX: {payload_hex}",
            flush=True,
        )
        return None

    packet = Packet.decode(payload)

    if packet is None:
        print(
            f"[MESH] invalid packet "
            f"len={len(payload)} "
            f"hex={payload_hex}",
            flush=True,
        )
        return None

    return packet


def main():

    # ============================================================
    # Redis
    # ============================================================

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    if getattr(config, "USE_DUMMY_GPS", False):
        dummy_position = {
            "lat": config.DUMMY_LAT,
            "lon": config.DUMMY_LON,
            "source": "dummy",
        }

        r.set(
            config.REDIS_GPS_STATE_KEY,
            json.dumps(dummy_position)
        )
    
        print(
            f"[DUMMY-GPS] "
            f"lat={config.DUMMY_LAT} "
            f"lon={config.DUMMY_LON}",
            flush=True,
        )


    # ============================================================
    # Routing
    # ============================================================

    routing = Routing(r)


    # ============================================================
    # Redis Pub/Sub
    # ============================================================

    pubsub = r.pubsub(
        ignore_subscribe_messages=True
    )

    channels = [config.REDIS_EVENT]
    if getattr(config, "NODE_ROLE", None) == "BASE":
        channels.extend((ORDER_CHANNEL, OFFER_CHANNEL))
    pubsub.subscribe(*channels)
    print(f"[MESH-CHANNELS] {channels}", flush=True)


    print(
        "[MESH-DAEMON] started",
        flush=True
    )


    try:

        while True:

            message = pubsub.get_message(
                timeout=1.0
            )


            if message is None:

                time.sleep(0.05)

                continue


            # ====================================================
            # JSON event
            # ====================================================

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


            channel = message.get("channel")
            if channel == ORDER_CHANNEL:
                try:
                    process_order(r, routing, event)
                except Exception as exc:
                    print(f"[ORDER-ASK-ERR] {type(exc).__name__}: {exc}", flush=True)
                continue
            if channel == OFFER_CHANNEL:
                try:
                    process_offer(r, routing, event)
                except Exception as exc:
                    print(f"[ORDER-REPLY-ERR] {type(exc).__name__}: {exc}", flush=True)
                continue

            if not isinstance(event, dict):
                continue
            event_type = event.get("event")


            # ====================================================
            # 自分自身が生成したPacket
            #
            # ask_sender
            #     ↓
            # local_packet
            #     ↓
            # Duplicate Suppressionへ登録
            # ====================================================

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


            # ====================================================
            # LoRa RX
            # ====================================================

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



            # ====================================================
            # Routing
            # ====================================================

            routing.handle_packet(
                packet
            )


    except KeyboardInterrupt:

        print(
            "\n[MESH-DAEMON] stopped",
            flush=True
        )


    finally:

        pubsub.close()


if __name__ == "__main__":
    main()
