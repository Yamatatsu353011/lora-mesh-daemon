# -*- coding: utf-8 -*-
import redis

import config
from mesh import DuplicateSuppressor
from packet import Packet, make_replay_packet


class Routing:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.duplicate_suppressor = DuplicateSuppressor(
            retention_sec=300.0
        )

    def handle_packet(self, packet: Packet) -> None:
        """
        受信したASK/REPLYパケットを処理する。
        """

        # 同一パケットを受信済みなら破棄
        if self.duplicate_suppressor.is_duplicate(
            packet.msg_type,
            packet.pkt_id,
        ):
            print(
                f"[MESH-DROP] duplicate "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id}",
                flush=True,
            )
            return

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

        if packet.msg_type == "ASK":
            self._handle_ask(packet)
            return

        if packet.msg_type == "REPLY":
            self._handle_reply(packet)
            return

        print(
            f"[MESH-DROP] unknown type={packet.msg_type}",
            flush=True,
        )
        
    def _handle_ask(self, packet: Packet) -> None:
        """
        ASKを受信したときの処理。
        指定されたデータを持っていて、
        かつ自分が目標エリアの場合はREPLYを生成する。
        条件を満たさない場合はASKを中継する。
        """
        is_target_area = (
            packet.target_bst == config.LOCAL_BST_ID
        )

        has_data = (
            packet.data_id in config.MY_DATA_IDS
        )

        if is_target_area and has_data:
            print(
                f"[ASK-DATA-FOUND] "
                f"id={packet.pkt_id} "
                f"target={packet.target_bst} "
                f"data={packet.data_id} "
                f"source={packet.source_bst}",
                flush=True,
            )

            reply_packet = make_reply_packet(
                pkt_id=packet.pkt_id,
                target_bst=packet.source_bst,
                data_id=packet.data_id,
                responder_bst=config.LOCAL_BST_ID,
            )

            reply_line = reply_packet.encode()

            self.redis.lpush(
                config.REDIS_RAW_TX,
                reply_line,
            )

            print(
                f"[REPLY-SEND] "
                f"id={reply_packet.pkt_id} "
                f"target={reply_packet.target_bst} "
                f"data={reply_packet.data_id} "
                f"responder={reply_packet.responder_bst} "
                f"ttl={reply_packet.ttl} "
                f"line={reply_line}",
                flush=True,
            )

            return

        print(
            f"[ASK-FORWARD] "
            f"id={packet.pkt_id} "
            f"target_match={is_target_area} "
            f"has_data={has_data}",
            flush=True,
        )

        self._forward(packet)

    def _handle_reply(self, packet: Packet) -> None:
        """
        REPLYパケットを処理する。
        現時点では、自分宛てなら到着、それ以外は中継する。
        """

        if packet.target_bst == config.LOCAL_BST_ID:
            print(
                f"[REPLY-ARRIVED] "
                f"id={packet.pkt_id} "
                f"target={packet.target_bst} "
                f"data={packet.data_id} "
                f"responder={packet.responder_bst}",
                flush=True,
            )
            return

        self._forward(packet)

    def _forward(self, packet: Packet) -> None:
        """
        TTLを確認し、中継パケットをRedis送信キューへ追加する。
        """

        if not packet.can_forward():
            print(
                f"[MESH-DROP] ttl expired "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id}",
                flush=True,
            )
            return

        forward_packet = packet.forwarded()

        if forward_packet is None:
            return

        forward_line = forward_packet.encode()

        self.redis.lpush(
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
