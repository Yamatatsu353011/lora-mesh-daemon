# -*- coding: utf-8 -*-

import json
import time

import redis

import config
from easel_radio import EaselRadio
from slotted_aloha import SlottedAloha


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(obj) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
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
    # Slotted ALOHA
    # ============================================================

    aloha = SlottedAloha(
        frame_sec=config.FRAME_SEC,
        slot_sec=config.SLOT_SEC,
        guard_sec=config.TX_GUARD_SEC,
        enabled=config.SLOTTED_ENABLED,
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

    print(
        f"[ALOHA] "
        f"enabled={config.SLOTTED_ENABLED} "
        f"frame={config.FRAME_SEC}s "
        f"slot={config.SLOT_SEC}s "
        f"guard={config.TX_GUARD_SEC}s",
        flush=True,
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
                timeout_sec=0.02
            )


            if payload:

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
            # Redis queue
            #      ↓
            # Slotted ALOHA
            #      ↓
            # HEX文字列
            #      ↓
            # bytes
            #      ↓
            # LoRa Binary
            # ====================================================

            if aloha.can_transmit():

                # 自分の送信slotになってから
                # Redisキューから取り出す
                tx_hex = r.rpop(
                    config.REDIS_RAW_TX
                )


                if tx_hex:

                    try:

                        tx_payload = bytes.fromhex(
                            tx_hex
                        )


                    except ValueError:

                        print(
                            f"[TX-DROP] "
                            f"invalid hex: {tx_hex}",
                            flush=True
                        )

                        tx_payload = None


                    if tx_payload:

                        # -------------------------
                        # 最大50 byte
                        # -------------------------

                        if (
                            len(tx_payload)
                            > config.MAX_BINARY_PAYLOAD_LEN
                        ):

                            print(
                                f"[TX-DROP] "
                                f"too long "
                                f"len={len(tx_payload)} "
                                f"max="
                                f"{config.MAX_BINARY_PAYLOAD_LEN}",
                                flush=True
                            )


                            r.publish(
                                config.REDIS_EVENT,
                                json_dumps({
                                    "event":
                                        "tx_drop_too_long",

                                    "node_id":
                                        config.NODE_ID,

                                    "payload_len":
                                        len(tx_payload),

                                    "max_len":
                                        config.MAX_BINARY_PAYLOAD_LEN,

                                    "at_ms":
                                        now_ms(),
                                })
                            )


                        else:

                            # -------------------------
                            # ALOHA状態
                            # -------------------------

                            aloha_state = (
                                aloha.status()
                            )


                            print(
                                f"[ALOHA-TX] "
                                f"frame="
                                f"{aloha_state['frame']} "
                                f"slot="
                                f"{aloha_state['current_slot']} "
                                f"tx_slot="
                                f"{aloha_state['tx_slot']} "
                                f"len="
                                f"{len(tx_payload)}",
                                flush=True,
                            )


                            # -------------------------
                            # Binary LoRa送信
                            # -------------------------

                            ok = (
                                radio.send_binary_payload(
                                    tx_payload
                                )
                            )


                            # -------------------------
                            # このframeでは送信済み
                            # -------------------------

                            aloha.mark_transmitted()


                            # -------------------------
                            # TXイベント
                            # -------------------------

                            r.publish(
                                config.REDIS_EVENT,
                                json_dumps({
                                    "event": "tx",

                                    "node_id":
                                        config.NODE_ID,

                                    "payload_hex":
                                        tx_hex,

                                    "ok":
                                        ok,

                                    "frame":
                                        aloha_state["frame"],

                                    "slot":
                                        aloha_state[
                                            "current_slot"
                                        ],

                                    "tx_slot":
                                        aloha_state[
                                            "tx_slot"
                                        ],

                                    "sent_at_ms":
                                        now_ms(),
                                })
                            )


                            print(
                                f"[RAW-TX] "
                                f"len={len(tx_payload)} "
                                f"ok={ok} "
                                f"hex={tx_hex}",
                                flush=True
                            )


            # ====================================================
            # State
            # ====================================================

            aloha_state = (
                aloha.status()
            )

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

                # Slotted ALOHA
                "aloha_enabled":
                    config.SLOTTED_ENABLED,

                "frame_sec":
                    config.FRAME_SEC,

                "slot_sec":
                    config.SLOT_SEC,

                "frame":
                    aloha_state["frame"],

                "current_slot":
                    aloha_state["current_slot"],

                "tx_slot":
                    aloha_state["tx_slot"],

                "updated_at_ms":
                    now_ms(),
            }


            r.set(
                config.REDIS_STATE,
                json_dumps(state)
            )


            # 0.2秒slotを取り逃がさないよう
            # 5ms周期でメインループ
            time.sleep(0.005)


    except KeyboardInterrupt:

        print(
            "\n[RAW-DAEMON] stopped",
            flush=True
        )


    finally:

        radio.close()


if __name__ == "__main__":
    main()