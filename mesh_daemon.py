# -*- coding: utf-8 -*-
import json
import time

import redis

import config
from packet import Packet


def main():
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(config.REDIS_EVENT)

    print("[MESH-DAEMON] started", flush=True)

    try:
        while True:
            message = pubsub.get_message(timeout=1.0)

            if message is None:
                time.sleep(0.05)
                continue

            try:
                event = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                print(
                    f"[MESH] invalid event: {message.get('data')}",
                    flush=True,
                )
                continue

            # LoRa受信イベント以外は処理しない
            if event.get("event") != "rx":
                continue

            line = event.get("line")

            if not isinstance(line, str):
                continue

            packet = Packet.decode(line)

            # ビーコンなど、ASK/REPLY以外のデータは無視
            if packet is None:
                print(f"[MESH] non-mesh packet: {line}", flush=True)
                continue

            print(
                f"[MESH-RX] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"target={packet.target_bst} "
                f"data={packet.data_id} "
                f"source={packet.source_bst} "
                f"responder={packet.responder_bst}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("[MESH-DAEMON] stopped", flush=True)

    finally:
        pubsub.close()


if __name__ == "__main__":
    main()
