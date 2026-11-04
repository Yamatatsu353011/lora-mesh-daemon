# -*- coding: utf-8 -*-

import json
import math
import time

import redis

import config
from bst_id.encoder import BSTIDEncoder
from packet import make_ask_packet

ORDER_CHANNEL = "event:business:order_created"
COUNTER_KEY = f"lora:order:ask_seq:{config.OWN_ID}"
TTL = 10

# 物資コードの変換表。全ノードで同じ表を使うこと。
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


def process_order(r, msg):
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
        r.publish(config.REDIS_EVENT, json.dumps({"event": "local_packet", "payload_hex": payload_hex}))
        r.lpush(config.REDIS_RAW_TX, payload_hex)
        print(
            f"[ORDER-ASK-QUEUED] order={order_id} pkt={pkt_id} "
            f"item={code} qty={qty} data_id={data_id} "
            f"goal=({goal_lat:.7f},{goal_lon:.7f}) ttl={TTL} bytes={len(payload)}",
            flush=True,
        )


def main():
    if getattr(config, "NODE_ROLE", None) != "BASE":
        raise RuntimeError("発注ASK送信はNODE_ROLE=BASEでのみ起動してください")
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    r.ping()
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(ORDER_CHANNEL)
    print(f"[ORDER-ASK] subscribed channel={ORDER_CHANNEL} node={config.OWN_ID}", flush=True)
    try:
        for event in pubsub.listen():
            if event.get("type") != "message":
                continue
            try:
                msg = json.loads(event["data"])
                process_order(r, msg)
            except Exception as exc:
                print(f"[ORDER-ASK-ERR] {type(exc).__name__}: {exc}", flush=True)
    except KeyboardInterrupt:
        print("[ORDER-ASK] stopped", flush=True)
    finally:
        pubsub.close()


if __name__ == "__main__":
    main()

