
# -*- coding: utf-8 -*-
"""移動ノード専用: GPS位置を監視用REPLYとして周期送信する。"""

import json
import time

import redis

import config
from bst_id.encoder import BSTIDEncoder
from packet import make_reply_packet

SEND_INTERVAL_SEC = 3.0
GPS_MAX_AGE_SEC = 15.0
MONITOR_DATA_ID = "POS"


def make_position_reply(r, sequence):
    raw = r.get(config.REDIS_GPS_STATE_KEY)

    if not raw:
        print(
            "[POSITION-SKIP] GPS state unavailable",
            flush=True,
        )
        return None

    state = json.loads(raw)

    if state.get("source") != "gps":
        print(
            "[POSITION-SKIP] position is not from real GPS",
            flush=True,
        )
        return None

    lat = float(state["lat"])
    lon = float(state["lon"])

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude/longitude out of range")

    updated_at = float(state["updated_at"])
    age = time.time() - updated_at

    if age < -5.0 or age > GPS_MAX_AGE_SEC:
        print(
            f"[POSITION-SKIP] GPS state stale (age={age:.1f}s)",
            flush=True,
        )
        return None

    bst_id, bit_len = BSTIDEncoder.encode(
        lon, lat, None, None,
        config.BST_ZOOM_X,
        config.BST_ZOOM_Y,
        0, 0,
    )

    pkt_id = f"{config.OWN_ID}-{sequence}"

    packet = make_reply_packet(
        pkt_id=pkt_id,
        goal_bst=bst_id,
        goal_bit_len=bit_len,
        start_bst=bst_id,
        start_bit_len=bit_len,
        data_id=MONITOR_DATA_ID,
        ttl=0,
        distance=0.0,
    )

    return packet, lat, lon


def main():
    if getattr(config, "NODE_ROLE", None) != "MOBILE":
        raise RuntimeError(
            "NODE_ROLE must be MOBILE on the sender"
        )

    if config.USE_DUMMY_GPS:
        raise RuntimeError(
            "MOBILE must use real GPS"
        )

    if SEND_INTERVAL_SEC <= 0:
        raise ValueError(
            "SEND_INTERVAL_SEC must be positive"
        )

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    r.ping()

    sequence = int(time.time() * 1000)

    print(
        f"[POSITION-SENDER] started node={config.OWN_ID}",
        flush=True,
    )

    while True:
        try:
            result = make_position_reply(r, sequence)

            if result is not None:
                packet, lat, lon = result
                payload = packet.encode()

                r.lpush(
                    config.REDIS_RAW_TX,
                    payload.hex(),
                )

                print(
                    f"[POSITION-QUEUE] "
                    f"id={packet.pkt_id} "
                    f"lat={lat:.8f} "
                    f"lon={lon:.8f} "
                    f"bytes={len(payload)}",
                    flush=True,
                )

                sequence += 1

        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            print(
                f"[POSITION-SKIP] invalid GPS/packet: {exc}",
                flush=True,
            )

        except redis.RedisError as exc:
            print(
                f"[POSITION-ERROR] Redis: {exc}",
                flush=True,
            )

        time.sleep(SEND_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "[POSITION-SENDER] stopped",
            flush=True,
        )
