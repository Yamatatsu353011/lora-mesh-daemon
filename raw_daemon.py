# -*- coding: utf-8 -*-
import json
import time

import redis

import config
from easel_radio import EaselRadio


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main():
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    radio = EaselRadio(debug=True)

    radio.open()
    radio.configure_from_config()

    print("[RAW-DAEMON] started", flush=True)

    try:
        while True:
            # ----------------------------------------------------
            # RX from LoRa
            # ----------------------------------------------------
            line = radio.read_line()
            if line:
                obj = {
                    "node_id": config.NODE_ID,
                    "line": line,
                    "received_at_ms": now_ms(),
                }

                r.lpush(config.REDIS_RAW_RX, json_dumps(obj))
                r.ltrim(config.REDIS_RAW_RX, 0, 99)

                r.publish(
                    config.REDIS_EVENT,
                    json_dumps({
                        "event": "rx",
                        **obj,
                    })
                )

            # ----------------------------------------------------
            # TX from Redis
            # ----------------------------------------------------
            tx_line = r.rpop(config.REDIS_RAW_TX)

            if tx_line:
                if len(tx_line) <= config.MAX_TX_LINE_LEN:
                    ok = radio.send_payload(tx_line, max_len=config.MAX_TX_LINE_LEN)

                    r.publish(
                        config.REDIS_EVENT,
                        json_dumps({
                            "event": "tx",
                            "node_id": config.NODE_ID,
                            "line": tx_line,
                            "ok": ok,
                            "sent_at_ms": now_ms(),
                        })
                    )
                else:
                    print(
                        f"[DROP] too long len={len(tx_line)} max={config.MAX_TX_LINE_LEN}",
                        flush=True
                    )

                    r.publish(
                        config.REDIS_EVENT,
                        json_dumps({
                            "event": "tx_drop_too_long",
                            "node_id": config.NODE_ID,
                            "line_len": len(tx_line),
                            "max_len": config.MAX_TX_LINE_LEN,
                            "at_ms": now_ms(),
                        })
                    )

            # ----------------------------------------------------
            # State
            # ----------------------------------------------------
            state = {
                "node_id": config.NODE_ID,
                "port": config.SERIAL_PORT,
                "baudrate": config.BAUDRATE,
                "bw": config.BW,
                "sf": config.SF,
                "ch": config.CH,
                "panid": config.PAN_ID,
                "ownid": config.OWN_ID,
                "dstid": config.DST_ID,
                "updated_at_ms": now_ms(),
            }

            r.set(config.REDIS_STATE, json_dumps(state))

            time.sleep(0.05)

    finally:
        radio.close()


if __name__ == "__main__":
    main()
