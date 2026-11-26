# -*- coding: utf-8 -*-
"""YOLO が検出した瞬間に呼び出す LoRa 検出通知送信 API。"""

import json
import math
import time

import redis

import config
from bst_id.encoder import BSTIDEncoder
from packet import Packet

GPS_MAX_AGE_SEC = 15.0
DETECTION_TTL = 10
DETECTION_COOLDOWN_SEC = 30


def _base36(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value == 0:
        return "0"
    digits = []
    while value:
        value, mod = divmod(value, 36)
        digits.append(alphabet[mod])
    return "".join(reversed(digits))


def send_detection(redis_client=None):
    """検出を送信キューへ登録。成功時はpacket ID、スキップ時はNone。

    この関数は無線到達確認を行わない。YOLO 側で検出成立時に呼ぶ。
    """
    r = redis_client or redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    raw = r.get(config.REDIS_GPS_STATE_KEY)
    if not raw:
        print("[DETECTION-SKIP] GPS state unavailable", flush=True)
        return None

    try:
        state = json.loads(raw)
        if state.get("source") != "gps":
            print("[DETECTION-SKIP] real GPS required", flush=True)
            return None

        lat = float(state["lat"])
        lon = float(state["lon"])
        updated_at = float(state["updated_at"])
        age = time.time() - updated_at

        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError("non-finite coordinates")

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("coordinates out of range")

        if not (-5 <= age <= GPS_MAX_AGE_SEC):
            print(
                f"[DETECTION-SKIP] GPS state stale age={age:.1f}s",
                flush=True,
            )
            return None

    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"[DETECTION-SKIP] invalid GPS state: {exc}",
            flush=True,
        )
        return None

    bst_id, bit_len = BSTIDEncoder.encode(
        lon, lat, None, None,
        config.BST_ZOOM_X,
        config.BST_ZOOM_Y,
        0, 0,
    )

    # 送信頻度制限をノード単位で共有
    cooldown_key = f"lora:detection:cooldown:{config.OWN_ID}"

    if not r.set(
        cooldown_key,
        "1",
        nx=True,
        ex=DETECTION_COOLDOWN_SEC,
    ):
        print("[DETECTION-SKIP] cooldown", flush=True)
        return None

    try:
        # Redis連番で検出通知を識別
        sequence = r.incr(
            f"lora:detection:seq:{config.OWN_ID}"
        )

        packet_id = f"{config.OWN_ID}-D{_base36(sequence)}"

        packet = Packet(
            msg_type="DETECTION",
            pkt_id=packet_id,
            goal_bst=0,
            goal_bit_len=0,
            start_bst=bst_id,
            start_bit_len=bit_len,
            data_id="DET",
            ttl=DETECTION_TTL,
            distance=0.0,
        )

        payload = packet.encode()

        # 既存のLoRa送信キューを利用
        pipe = r.pipeline(transaction=True)

        pipe.lpush(
            config.REDIS_RAW_TX,
            payload.hex(),
        )

        # 自分が生成したパケットを重複排除に登録
        pipe.publish(
            config.REDIS_EVENT,
            json.dumps({
                "event": "local_packet",
                "payload_hex": payload.hex(),
            }),
        )

        pipe.execute()

        print(
            f"[DETECTION-QUEUED] "
            f"id={packet_id} "
            f"lat={lat:.8f} "
            f"lon={lon:.8f} "
            f"bytes={len(payload)}",
            flush=True,
        )

        return packet_id

    except Exception:
        r.delete(cooldown_key)
        raise


if __name__ == "__main__":
    send_detection()
