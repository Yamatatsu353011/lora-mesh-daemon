# -*- coding: utf-8 -*-

import json
import time

import redis

import config
from packet import Packet
from routing import Routing


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

    pubsub.subscribe(
        config.REDIS_EVENT
    )


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


            event_type = event.get(
                "event"
            )


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


            print(
                f"[MESH-RX-PACKET] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"goal={packet.goal_bst} "
                f"start={packet.start_bst} "
                f"data={packet.data_id} "
                f"ttl={packet.ttl} "
                f"distance={packet.distance:.2f}",
                flush=True,
            )


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