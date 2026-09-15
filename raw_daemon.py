# -*- coding: utf-8 -*-

import json
import time

import redis

import config
from easel_radio import EaselRadio


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(obj) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":")
    )


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


    # ============================================================
    # LoRa
    # ============================================================

    radio = EaselRadio(
        debug=True
    )

    radio.open()
    radio.configure_from_config()

    print(
        "[RAW-DAEMON] started",
        flush=True
    )


    try:

        while True:

            # ====================================================
            # RX
            #
            # LoRa Binary
            #      ↓
            # bytes
            #      ↓
            # HEX文字列
            #      ↓
            # Redis
            # ====================================================

            payload = radio.read_binary_payload(
                timeout_sec=0.1
            )


            if payload is not None:

                payload_hex = payload.hex()

                obj = {
                    "node_id": config.NODE_ID,
                    "payload_hex": payload_hex,
                    "received_at_ms": now_ms(),
                }


                # -------------------------
                # RX履歴
                # -------------------------

                r.lpush(
                    config.REDIS_RAW_RX,
                    json_dumps(obj)
                )

                r.ltrim(
                    config.REDIS_RAW_RX,
                    0,
                    99
                )


                # -------------------------
                # mesh_daemonへ通知
                # -------------------------

                r.publish(
                    config.REDIS_EVENT,
                    json_dumps({
                        "event": "rx",
                        **obj,
                    })
                )


                print(
                    f"[RAW-RX] "
                    f"len={len(payload)} "
                    f"hex={payload_hex}",
                    flush=True
                )


            # ====================================================
            # TX
            #
            # Redis
            #   ↓
            # HEX文字列
            #   ↓
            # bytes
            #   ↓
            # LoRa Binary
            # ====================================================

            tx_hex = r.rpop(
                config.REDIS_RAW_TX
            )


            if tx_hex:

                try:

                    payload = bytes.fromhex(
                        tx_hex
                    )


                except ValueError:

                    print(
                        f"[TX-DROP] "
                        f"invalid hex: {tx_hex}",
                        flush=True
                    )

                    continue


                # ES920LRは最大50 byte
                if len(payload) > 50:

                    print(
                        f"[TX-DROP] "
                        f"too long "
                        f"len={len(payload)} "
                        f"max=50",
                        flush=True
                    )


                    r.publish(
                        config.REDIS_EVENT,
                        json_dumps({
                            "event": "tx_drop_too_long",
                            "node_id": config.NODE_ID,
                            "payload_len": len(payload),
                            "max_len": 50,
                            "at_ms": now_ms(),
                        })
                    )

                    continue


                # -------------------------
                # Binary LoRa送信
                # -------------------------

                ok = radio.send_binary_payload(
                    payload
                )


                # -------------------------
                # TXイベント
                # -------------------------

                r.publish(
                    config.REDIS_EVENT,
                    json_dumps({
                        "event": "tx",
                        "node_id": config.NODE_ID,
                        "payload_hex": tx_hex,
                        "ok": ok,
                        "sent_at_ms": now_ms(),
                    })
                )


                print(
                    f"[RAW-TX] "
                    f"len={len(payload)} "
                    f"ok={ok} "
                    f"hex={tx_hex}",
                    flush=True
                )


            # ====================================================
            # State
            # ====================================================

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
                "format": config.FORMAT,
                "updated_at_ms": now_ms(),
            }


            r.set(
                config.REDIS_STATE,
                json_dumps(state)
            )


            time.sleep(0.05)


    except KeyboardInterrupt:

        print(
            "\n[RAW-DAEMON] stopped",
            flush=True
        )


    finally:

        radio.close()


if __name__ == "__main__":
    main()