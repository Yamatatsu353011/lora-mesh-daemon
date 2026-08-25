# -*- coding: utf-8 -*-
import json
import time
import redis

import config
from packet import make_ask_packet


def main():
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
    )

    # 最初に1回だけ入力
    target_bst = int(input("Target BST ID: "))
    data_id = input("Data ID: ").strip()

    counter = 1

    print(
        f"[ASK-LOOP] target={target_bst} "
        f"data={data_id} interval=1s"
    )

    try:
        while True:
            # 毎回新しいPacket IDを生成
            pkt_id = f"{config.LOCAL_BST_ID}-{counter}"

            packet = make_ask_packet(
                pkt_id=pkt_id,
                target_bst=target_bst,
                data_id=data_id,
                source_bst=config.LOCAL_BST_ID,
            )

            line = packet.encode()

            # 自分が送ったパケットとして登録するため通知
            r.publish(
                config.REDIS_EVENT,
                json.dumps({
                    "event": "local_packet",
                    "line": line,
                })
            )

            # raw_daemonの送信キューへ追加
            r.lpush(
                config.REDIS_RAW_TX,
                line,
            )

            print(
                f"[ASK-SEND] "
                f"id={pkt_id} "
                f"target={target_bst} "
                f"data={data_id} "
                f"line={line}",
                flush=True,
            )

            counter += 1

            # 1秒待って次のASK
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[ASK-LOOP] stopped")


if __name__ == "__main__":
    main()
