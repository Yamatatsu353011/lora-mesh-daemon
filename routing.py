# -*- coding: utf-8 -*-

import json
import math

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
    # 自ノードの現在緯度経度取得
    # ============================================================

    def _get_local_position(self):
        """
        Redis state:self から自ノードの lat/lon を取得する。
        """

        raw = self.redis.get(
            config.REDIS_GPS_STATE_KEY
        )

        if not raw:
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
            return None

        return lat, lon


    # ============================================================
    # 2点間距離
    # ============================================================

    @staticmethod
    def _haversine_distance_m(lat1, lon1, lat2, lon2):
        """
        緯度経度2点間の距離[m]をHaversine式で求める。
        """

        earth_radius_m = 6371000.0

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(d_phi / 2.0) ** 2
            + math.cos(phi1)
            * math.cos(phi2)
            * math.sin(d_lambda / 2.0) ** 2
        )

        c = 2.0 * math.atan2(
            math.sqrt(a),
            math.sqrt(1.0 - a),
        )

        return earth_radius_m * c


    # ============================================================
    # 自ノード -> 指定BST-ID の距離
    # ============================================================

    def _distance_to_bst(self, goal_bst, goal_bit_len):
        """
        自ノードの現在位置から指定BST-IDまでの距離[m]を返す。
        計算できない場合はNone。
        """

        local_position = self._get_local_position()

        if local_position is None:
            return None

        local_lat, local_lon = local_position

        try:
            goal_lon, goal_lat, _, _ = BSTIDDecoder.decode(
                goal_bst,
                goal_bit_len,
            )

        except Exception:
            return None

        if goal_lat is None or goal_lon is None:
            return None

        return self._haversine_distance_m(
            local_lat,
            local_lon,
            goal_lat,
            goal_lon,
        )


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

            return


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
            # REPLY生成ノード -> ASK生成元位置 の距離
            # ----------------------------------------------------

            reply_distance = self._distance_to_bst(
                packet.start_bst,
                packet.start_bit_len,
            )

            if reply_distance is None:

                print(
                    f"[DIRECTION-DROP] "
                    f"type=REPLY "
                    f"id={packet.pkt_id} "
                    f"reason=distance_unavailable",
                    flush=True,
                )

                return


            # ----------------------------------------------------
            # REPLY生成
            # ----------------------------------------------------

            reply_packet = make_reply_packet(
                pkt_id=packet.pkt_id,

                goal_bst=packet.start_bst,
                goal_bit_len=packet.start_bit_len,

                # REPLY生成ノードの現在位置
                start_bst=local_bst,
                start_bit_len=local_bit_len,

                data_id=packet.data_id,

                # REPLY生成地点からgoalまでの初期距離
                distance=reply_distance,
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
                f"ttl={reply_packet.ttl} "
                f"distance={reply_packet.distance:.2f}",
                flush=True,
            )

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

        data_id="POS" は移動ノードの位置通知として扱う。
        それ以外のREPLYは従来のASK応答として処理する。
        """

        if packet.data_id == "POS":
            role = getattr(config, "NODE_ROLE", None)

            if role == "BASE":
                # 基地局は位置を保存するが再送信しない。
                self._save_mobile_position(packet)

            elif role == "RELAY":
                # 監視用POSには宛先BST・距離がないため、
                # 通常REPLYの指向性転送ではなくTTL限定で転送する。
                self._forward_monitor_position(packet)

            else:
                print(
                    f"[MONITOR-SKIP] role={role} id={packet.pkt_id}",
                    flush=True,
                )
            return

        # ========================================================
        # Packet IDからASK生成元ノードIDを取得
        # ========================================================


        origin_node_id = packet.pkt_id.split(
            "-",
            1,
        )[0]


        # ========================================================
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
    # 監視用POSの中継（RELAYのみ）
    # ============================================================

    def _forward_monitor_position(self, packet: Packet) -> None:
        """
        POSパケットをTTL限定で中継する。
        重複排除はhandle_packet()で既に実施されている。
        pkt_id・start_bstは変更せず、TTLだけ1減らす。
        """
        if not packet.can_forward():
            print(
                f"[MONITOR-NO-FORWARD] id={packet.pkt_id} "
                f"ttl={packet.ttl}",
                flush=True,
            )
            return

        forward_packet = packet.forwarded()
        if forward_packet is None:
            return

        try:
            self._send_packet(forward_packet)
        except Exception as e:
            print(
                f"[MONITOR-FORWARD-ERR] id={packet.pkt_id} error={e}",
                flush=True,
            )
            return

        print(
            f"[MONITOR-FORWARD] id={forward_packet.pkt_id} "
            f"ttl={packet.ttl}->{forward_packet.ttl} "
            f"start_bst={forward_packet.start_bst}",
            flush=True,
        )


    # ============================================================
    # Forward
    # ============================================================

    def _forward(self, packet: Packet) -> None:
        """
        TTLに加えて位置指向性で中継判断する。

        packet.distance:
            直前の送信ノードからgoalまでの距離[m]

        local_distance:
            自ノードからgoalまでの距離[m]

        """

        if not packet.can_forward():
            return

        # 自ノードからgoalまでの距離
        local_distance = self._distance_to_bst(
            packet.goal_bst,
            packet.goal_bit_len,
        )

        if local_distance is None:

            print(
                f"[DIRECTION-DROP] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"reason=distance_unavailable",
                flush=True,
            )

            return

        try:
            previous_distance = float(packet.distance)

        except (
            TypeError,
            ValueError,
        ):

            print(
                f"[DIRECTION-DROP] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"reason=invalid_previous_distance",
                flush=True,
            )

            return

        if previous_distance <= 0.0:

            print(
                f"[DIRECTION-DROP] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"prev={previous_distance:.2f} "
                f"local={local_distance:.2f} "
                f"reason=invalid_previous_distance",
                flush=True,
            )

            return

        # 最低1mはgoal方向へ前進したノードだけ中継
        min_progress_m = 1.0

        if (
            local_distance + min_progress_m
            >= previous_distance
        ):

            print(
                f"[DIRECTION-DROP] "
                f"type={packet.msg_type} "
                f"id={packet.pkt_id} "
                f"prev={previous_distance:.2f} "
                f"local={local_distance:.2f}",
                flush=True,
            )

            return

        forward_packet = packet.forwarded()

        if forward_packet is None:
            return

        # 次のノードが比較できるように距離を書き換える
        forward_packet.distance = local_distance

        self._send_packet(
            forward_packet
        )

        print(
            f"[DIRECTION-FORWARD] "
            f"type={forward_packet.msg_type} "
            f"id={forward_packet.pkt_id} "
            f"prev={previous_distance:.2f} "
            f"local={local_distance:.2f} "
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



    # ============================================================
    # 移動ノードのREPLY位置通知を基地局で保存
    # ============================================================

    def _save_mobile_position(self, packet: Packet) -> None:
        """
        data_id="POS" のREPLYに含まれるstart_bstを位置へ復元し、
        各基地局のローカルRedisに保存する。

        監視用REPLYのpkt_idは「移動ノードOWN_ID-連番」とする。
        BST-IDを復元した座標であり、生のGPS座標ではない。
        """
        import time

        node_id = packet.pkt_id.split("-", 1)[0]
        if not node_id or "-" not in packet.pkt_id:
            print(f"[MONITOR-DROP] invalid pkt_id={packet.pkt_id}", flush=True)
            return

        try:
            lon, lat, _, _ = BSTIDDecoder.decode(
                packet.start_bst,
                packet.start_bit_len,
            )
            if lat is None or lon is None:
                raise ValueError("decoded coordinates are missing")
            lat = float(lat)
            lon = float(lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("decoded coordinates out of range")
        except (ValueError, TypeError, OverflowError) as e:
            print(f"[MONITOR-DROP] invalid position id={packet.pkt_id}: {e}", flush=True)
            return
        except Exception as e:
            print(f"[MONITOR-DROP] BST decode failed id={packet.pkt_id}: {e}", flush=True)
            return

        node_state = {
            "node_id": node_id,
            "lat": lat,
            "lon": lon,
            "bst_id_int": str(packet.start_bst),
            "bst_bit_len": packet.start_bit_len,
            "updated_at": time.time(),
            "packet_id": packet.pkt_id,
            "source": "REPLY_POS",
        }
        prefix = getattr(config, "REDIS_NODE_PREFIX", "lora:nodes:")
        key = f"{prefix}{node_id}"
        # Count only unique monitoring packets. handle_packet() has already
        # rejected duplicates using (msg_type, pkt_id).
        count_key = f"lora:monitor:rx_count:{node_id}"
        rx_count = self.redis.incr(count_key)
        node_state["rx_count"] = rx_count

        # Preserve the existing key used by the map/web integration.
        self.redis.set(key, json.dumps(node_state))

        print(
            f"[MONITOR-REPLY] node={node_id} "
            f"pkt={packet.pkt_id} lat={lat:.8f} lon={lon:.8f} "
            f"rx_count={rx_count}",
            flush=True,
        )
