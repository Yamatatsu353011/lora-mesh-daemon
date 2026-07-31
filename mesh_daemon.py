# -*- coding: utf-8 -*-
import json
import time

import redis

import config
from packet import Packet
from mesh import DuplicateSuppressor


def main():
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    duplicate_suppressor = DuplicateSuppressor(
        retention_sec=300.0
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
                    f"[MESH] invalid event: "
                    f"{message.get('data')}",
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

            # ビーコンなど、ASK/REPLY以外は無視
            if packet is None:
                print(
                    f"[MESH] non-mesh packet: {line}",
                    flush=True,
                )
                continue

            # 同じパケットを受信済みなら破棄
            if duplicate_suppressor.is_duplicate(
                packet.msg_type,
                packet.pkt_id,
            ):
                print(
                    f"[MESH-DROP] duplicate "
                    f"type={packet.msg_type} "
                    f"id={packet.pkt_id}",
                    flush=True,
                )
                continue

            # 受信内容を表示
            print(
                f"[MESH-RX] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"target={packet.target_bst} "
                f"data={packet.data_id} "
                f"source={packet.source_bst} "
                f"responder={packet.responder_bst} "
                f"ttl={packet.ttl}",
                flush=True,
            )

            # 自分宛てなら中継せず、到着として処理する
            if packet.target_bst == config.LOCAL_BST_ID:
                print(
                    f"[MESH-ARRIVED] "
                    f"type={packet.msg_type} "
                    f"id={packet.pkt_id} "
                    f"target={packet.target_bst} "
                    f"data={packet.data_id}",
                    flush=True,
                )
                continue

            # TTLが0なら中継しない
            if not packet.can_forward():
                print(
                    f"[MESH-DROP] ttl expired "
                    f"type={packet.msg_type} "
                    f"id={packet.pkt_id}",
                    flush=True,
                )
                continue

            # TTLを1減らしたパケットを生成
            forward_packet = packet.forwarded()

            if forward_packet is None:
                continue

            forward_line = forward_packet.encode()

            # raw_daemonの送信キューへ追加
            r.lpush(
                config.REDIS_RAW_TX,
                forward_line,
            )

            print(
                f"[MESH-FORWARD] "
                f"type={forward_packet.msg_type} "
                f"id={forward_packet.pkt_id} "
                f"ttl={forward_packet.ttl} "
                f"line={forward_line}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("[MESH-DAEMON] stopped", flush=True)

    finally:
        pubsub.close()


if __name__ == "__main__":
    main()
