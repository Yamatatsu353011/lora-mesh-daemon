# -*- coding: utf-8 -*-
import time

import redis

import config
from packet import make_ask_packet


query_count = 0


def generate_packet_id() -> str:
    """
    「ノードID-問い合わせ回数」の形式でパケットIDを生成する。

    例:
        100-1
        100-2
        100-3
    """
    global query_count

    query_count += 1

    return f"{config.LOCAL_BST_ID}-{query_count}"


def send_ask(
    redis_client: redis.Redis,
    target_bst: int,
    data_id: str,
) -> str:
    """
    ASKパケットを生成し、Redisの送信キューへ追加する。

    Returns:
        生成したパケットID
    """

    packet = make_ask_packet(
        pkt_id=generate_packet_id(),
        target_bst=target_bst,
        data_id=data_id,
        source_bst=config.LOCAL_BST_ID,
    )

    line = packet.encode()

    redis_client.lpush(
        config.REDIS_RAW_TX,
        line,
    )

    print(
        f"[ASK-SEND] "
        f"id={packet.pkt_id} "
        f"target={packet.target_bst} "
        f"data={packet.data_id} "
        f"source={packet.source_bst} "
        f"ttl={packet.ttl} "
        f"line={line}",
        flush=True,
    )

    return packet.pkt_id


def main() -> None:
    redis_client = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    try:
        redis_client.ping()

    except redis.RedisError as exc:
        print(
            f"[ASK-SENDER] Redis connection failed: {exc}",
            flush=True,
        )
        return

    print(
        f"[ASK-SENDER] started "
        f"local_bst={config.LOCAL_BST_ID}",
        flush=True,
    )

    try:
        while True:
            target_text = input(
                "Target BST-ID (qで終了): "
            ).strip()

            if target_text.lower() == "q":
                break

            try:
                target_bst = int(target_text)

            except ValueError:
                print(
                    "[ASK-SENDER] Target BST-ID must be an integer",
                    flush=True,
                )
                continue

            data_id = input(
                "Data ID: "
            ).strip()

            if not data_id:
                print(
                    "[ASK-SENDER] Data ID is empty",
                    flush=True,
                )
                continue

            try:
                send_ask(
                    redis_client=redis_client,
                    target_bst=target_bst,
                    data_id=data_id,
                )

            except redis.RedisError as exc:
                print(
                    f"[ASK-SENDER] Redis send failed: {exc}",
                    flush=True,
                )
                continue

            time.sleep(0.1)

    except KeyboardInterrupt:
        pass

    finally:
        redis_client.close()

        print(
            "[ASK-SENDER] stopped",
            flush=True,
        )


if __name__ == "__main__":
    main()
