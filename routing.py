# -*- coding: utf-8 -*-

import json

import redis

import config

from bst_id.encoder import BSTIDEncoder
from bst_id.decoder import BSTIDDecoder
from bst_id.logic import is_match

from mesh import DuplicateSuppressor
from packet import Packet, make_reply_packet


class Routing:

    def __init__(self, redis_client: redis.Redis):

        self.redis = redis_client

        self.duplicate_suppressor = DuplicateSuppressor(
            retention_sec=180.0
        )


    # ============================================================
    # 自ノードの現在BST-ID取得
    # ============================================================

    def _get_local_bst(self):
        """
        Redis state:self の lat/lon から
        自ノードのBST-IDを生成する。

        高度・時刻は使用しない。
        """

        raw = self.redis.get(
            config.REDIS_GPS_STATE_KEY
        )

        if not raw:

            print(
                "[GPS] local position unavailable",
                flush=True,
            )

            return None

        try:

            state = json.loads(raw)

            lat = float(state["lat"])
            lon = float(state["lon"])

        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):

            print(
                f"[GPS] invalid state: {raw}",
                flush=True,
            )

            return None

        bst_id, bit_len = BSTIDEncoder.encode(
            lon,
            lat,
            None,
            None,
            config.BST_ZOOM_X,
            config.BST_ZOOM_Y,
            0,
            0,
        )

        return bst_id, bit_len


    # ============================================================
    # Redis TX
    # ============================================================

    def _send_packet(self, packet: Packet) -> None:
        """
        Packet
          ↓ encode()
        bytes
          ↓ hex()
        Redis RAW_TX
        """

        payload = packet.encode()
        payload_hex = payload.hex()

        self.redis.lpush(
            config.REDIS_RAW_TX,
            payload_hex,
        )


    # ============================================================
    # Packet受信
    # ============================================================

    def handle_packet(self, packet: Packet) -> None:
        """
        受信したASK/REPLYパケットを処理する。
        """

        # --------------------------------------------------------
        # Duplicate Suppression
        # --------------------------------------------------------

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
            f"goal={packet.goal_bst} "
            f"start={packet.start_bst} "
            f"data={packet.data_id} "
            f"ttl={packet.ttl} "
            f"distance={packet.distance}",
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


    # ============================================================
    # ASK
    # ============================================================

    def _handle_ask(self, packet: Packet) -> None:
        """
        ASK受信処理。

        ・自分が目標BST-IDのエリア
        ・data_idを持っている

        の両方を満たせばREPLY。

        ★変更:
        REPLYを生成した場合、そのASKはそれ以上中継しない。
        """

        self._save_sender_position(packet)

        local = self._get_local_bst()

        is_target_area = False

        if local is not None:

            local_bst, local_bit_len = local

            is_target_area = is_match(
                packet.goal_bst,
                local_bst,
            )

        has_data = (
            packet.data_id
            in config.MY_DATA_IDS
        )

        print(
            f"[ASK-CHECK] "
            f"target_area={is_target_area} "
            f"has_data={has_data}",
            flush=True,
        )


        # --------------------------------------------------------
        # 目標エリア ＋ データあり
        # --------------------------------------------------------

        if (
            is_target_area
            and has_data
            and local is not None
        ):

            local_bst, local_bit_len = local

            print(
                f"[ASK-DATA-FOUND] "
                f"id={packet.pkt_id} "
                f"goal={packet.goal_bst} "
                f"data={packet.data_id} "
                f"ask_start={packet.start_bst}",
                flush=True,
            )


            # ----------------------------------------------------
            # REPLY生成
            # ----------------------------------------------------

            # REPLYのgoal_bstには、
            # ASK生成時の送信元位置を保持する。
            #
            # ★今後:
            # goal_bstは主に指向性・中継方向の判断に使用する。
            #
            # 最終到達判定はpkt_idのノードIDで行う。
            # ----------------------------------------------------

            reply_packet = make_reply_packet(
                pkt_id=packet.pkt_id,

                goal_bst=packet.start_bst,
                goal_bit_len=packet.start_bit_len,

                # REPLY生成ノードの現在位置
                start_bst=local_bst,
                start_bit_len=local_bit_len,

                data_id=packet.data_id,

                # 距離によるroutingはまだ使わない
                distance=0.0,
            )


            # ----------------------------------------------------
            # 自分自身が生成したREPLYを
            # Duplicate Suppressionへ登録
            # ----------------------------------------------------

            self.mark_sent(
                reply_packet
            )


            # ----------------------------------------------------
            # REPLY送信キューへ
            # ----------------------------------------------------

            self._send_packet(
                reply_packet
            )

            print(
                f"[REPLY-SEND] "
                f"id={reply_packet.pkt_id} "
                f"goal={reply_packet.goal_bst} "
                f"start={reply_packet.start_bst} "
                f"data={reply_packet.data_id} "
                f"ttl={reply_packet.ttl}",
                flush=True,
            )


            # ====================================================
            # ★変更1
            #
            # REPLYを生成した場合、
            # 元のASKはそれ以上中継しない。
            #
            # 以前:
            #
            #   REPLY生成
            #       ↓
            #   ASKもforward
            #
            # 変更後:
            #
            #   REPLY生成
            #       ↓
            #   return
            #
            # ====================================================

            return


        # --------------------------------------------------------
        # 目標条件を満たさなかったASKだけ中継
        # --------------------------------------------------------

        self._forward(packet)


    # ============================================================
    # REPLY
    # ============================================================

    def _handle_reply(self, packet: Packet) -> None:
        """
        REPLY受信処理。

        ★変更:
        goal_bstと現在位置の一致ではなく、
        pkt_idに含まれているASK生成元ノードIDで
        最終到達を判定する。

        例:

            pkt_id = "0003-125"

        の場合、

            origin_node_id = "0003"

        自分のOWN_IDが0003なら、
        このREPLYは自分宛てと判断する。

        goal_bstはREPLYの方向判断用として残す。
        """


        # ========================================================
        # ★変更2
        # Packet IDからASK生成元ノードIDを取得
        # ========================================================

        # 例:
        #
        #   "0003-125"
        #
        #        ↓
        #
        #   "0003"
        #

        origin_node_id = packet.pkt_id.split(
            "-",
            1,
        )[0]


        # ========================================================
        # ★変更2
        # REPLY最終到達判定
        # ========================================================

        if origin_node_id == str(config.OWN_ID):

            print(
                f"[REPLY-ARRIVED] "
                f"id={packet.pkt_id} "
                f"origin={origin_node_id} "
                f"goal={packet.goal_bst} "
                f"data={packet.data_id} "
                f"reply_start={packet.start_bst}",
                flush=True,
            )

            return


        # --------------------------------------------------------
        # 自分宛てではないREPLY
        # → 中継
        # --------------------------------------------------------

        self._forward(packet)


    # ============================================================
    # Forward
    # ============================================================

    def _forward(self, packet: Packet) -> None:
        """
        現在はTTLだけで中継判断する。

        distanceによる中継制御はまだ行わない。
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

        self._send_packet(
            forward_packet
        )

        print(
            f"[MESH-FORWARD] "
            f"type={forward_packet.msg_type} "
            f"id={forward_packet.pkt_id} "
            f"ttl={forward_packet.ttl}",
            flush=True,
        )


    # ============================================================
    # 自ノード送信Packet登録
    # ============================================================

    def mark_sent(self, packet: Packet) -> None:
        """
        自ノードが生成したPacketを
        Duplicate Suppressionへ登録する。
        """

        self.duplicate_suppressor.is_duplicate(
            packet.msg_type,
            packet.pkt_id,
        )


    # ============================================================
    # ASK送信元位置保存
    # ============================================================

    def _save_sender_position(self, packet: Packet) -> None:
        """
        ASK送信元のBST-IDをlat/lonへ戻し、
        Web表示用のRedisキーへ保存する。
        """

        try:

            lon, lat, _, _ = BSTIDDecoder.decode(
                packet.start_bst,
                packet.start_bit_len,
            )

        except Exception as e:

            print(
                f"[NODE-POS-ERR] BST decode failed: {e}",
                flush=True,
            )

            return

        if lat is None or lon is None:

            print(
                "[NODE-POS-ERR] lat/lon is None",
                flush=True,
            )

            return


        # pkt_id = "0001-20"
        #       ↓
        # node_id = "0001"

        node_id = packet.pkt_id.split(
            "-",
            1,
        )[0]

        node_state = {
            "node_id": node_id,
            "lat": lat,
            "lon": lon,
            "bst_id_int": str(packet.start_bst),
            "bst_bit_len": packet.start_bit_len,
        }

        
        key = f"lora:nodes:{node_id}"

        self.redis.set(
            key,
            json.dumps(node_state)
        )

        print(
            f"[NODE-POSITION] "
            f"node={node_id} "
            f"lat={lat} "
            f"lon={lon}",
            flush=True,
        )
