# -*- coding: utf-8 -*-
import json

import redis

import config


def main():
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    pubsub = r.pubsub()
    pubsub.subscribe(config.REDIS_EVENT)

    print("[TEST-RECEIVER] waiting...")

    for message in pubsub.listen():
        if message["type"] != "message":
            continue

        try:
            obj = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            continue

        if obj.get("event") != "rx":
            continue

        line = obj.get("line", "")

        if not line.startswith("TESTPING,"):
            continue

        data = line[len("TESTPING,"):]

        print(
            f"[TEST-PING] "
            f"data_len={len(data)} "
            f"total_len={len(line)}"
        )

        reply = "TESTPONG," + data

        r.lpush(
            config.REDIS_RAW_TX,
            reply,
        )

        print(
            f"[TEST-PONG] "
            f"data_len={len(data)} "
            f"total_len={len(reply)}"
        )


if __name__ == "__main__":
    main()
