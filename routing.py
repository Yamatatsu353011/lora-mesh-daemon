
# -*- coding: utf-8 -*-

import json
import math
import re
import time

import redis

import config

from bst_id.encoder import BSTIDEncoder
from bst_id.decoder import BSTIDDecoder
from bst_id.logic import is_match

from mesh import DuplicateSuppressor
from packet import Packet, make_reply_packet


ITEM_CODES = (
    "water",
    "hardtack",
    "rice_meal",
    "other_food",
    "supplement",
    "cold_medicine",
    "painkiller",
    "stomach_medicine",
    "eye_drops",
    "bandage",
    "compress",
    "blanket",
    "mat",
    "cushion",
    "portable_toilet",
    "battery",
    "light",
)


class Routing:

    def __init__(self, redis_client: redis.Redis):

        self.redis = redis_client

        self.duplicate_suppressor = DuplicateSuppressor(
            retention_sec=180.0
        )

    # ============================================================
    # Duplicate Suppression
    # ============================================================

    @staticmethod
    def _duplicate_type(packet: Packet):
        """
        同じASK IDを使うREPLYを区別する。

        ASK             -> ASK
        REPLY P01:2:2   -> REPLY:P
        REPLY C01:2:1   -> REPLY:C
        REPLY POS       -> REPLY:POS
        通常REPLY       -> REPLY

        DuplicateSuppressor自体の変更は不要。
        """

        if packet.msg_type != "REPLY":
            return packet.msg_type

        data_id = packet.data_id

        if data_id == "POS":
            return "REPLY:POS"

        if isinstance(data_id, str):

            if re.fullmatch(
                r"P\d{2}:\d+:[0-9A-Z]+",
                data_id,
            ):
                return "REPLY:P"

            if re.fullmatch(
                r"C\d{2}:\d+:[0-9A-Z]+",
                data_id,
            ):
                return "REPLY:C"

        return "REPLY"

    def _is_duplicate(self, packet: Packet):

        duplicate_type = self._duplicate_type(
            packet
        )

        return self.duplicate_suppressor.is_duplicate(
            duplicate_type,
            packet.pkt_id,
        )

    # ============================================================
    # Local BST-ID
    # ============================================================

    def _get_local_bst(self):
        """
        Redisから自ノードの位置情報を取得し、
        BST-IDへ変換する。
        """

        position = self._get_local_position()

        if position is None:

            print(
                "[GPS] local position unavailable",
                flush=True,
            )

            return None

        lat, lon = position

        zx = getattr(
            config,
            "BST_ZOOM_X",
            getattr(config, "BST_ZX", None),
        )

        zy = getattr(
            config,
            "BST_ZOOM_Y",
            getattr(config, "BST_ZY", None),
        )

        if zx is None or zy is None:

            print(
                "[GPS] BST zoom unavailable",
                flush=True,
            )

            return None

        try:

            bst_id, bit_len = BSTIDEncoder.encode(
                lon,
                lat,
                None,
                None,
                int(zx),
                int(zy),
                0,
                0,
            )

            return bst_id, bit_len

        except Exception as exc:

            print(
                f"[GPS] BST encode failed: {exc}",
                flush=True,
            )

            return None

    # ============================================================
    # Local Position
    # ============================================================

    def _get_local_position(self):

        raw = self.redis.get(
            config.REDIS_GPS_STATE_KEY
        )

        if not raw:
            return None

        try:

            state = json.loads(raw)

            lat = float(state["lat"])
            lon = float(state["lon"])

            if not (
                -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                return None

            return lat, lon

        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            return None

    # ============================================================
    # Haversine Distance
    # ============================================================

    @staticmethod
    def _haversine_distance_m(
        lat1,
        lon1,
        lat2,
        lon2,
    ):

        earth_radius_m = 6371000.0

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)

        d_phi = math.radians(
            lat2 - lat1
        )

        d_lambda = math.radians(
            lon2 - lon1
        )

        a = (
            math.sin(d_phi / 2.0) ** 2
            + math.cos(phi1)
            * math.cos(phi2)
            * math.sin(d_lambda / 2.0) ** 2
        )

        a = max(
            0.0,
            min(1.0, a),
        )

        c = 2.0 * math.atan2(
            math.sqrt(a),
            math.sqrt(1.0 - a),
        )

        return earth_radius_m * c

    # ============================================================
    # Distance To BST-ID
    # ============================================================

    def _distance_to_bst(
        self,
        goal_bst,
        goal_bit_len,
    ):

        local_position = (
            self._get_local_position()
        )

        if local_position is None:
            return None

        local_lat, local_lon = (
            local_position
        )

        try:

            goal_lon, goal_lat, _, _ = (
                BSTIDDecoder.decode(
                    goal_bst,
                    goal_bit_len,
                )
            )

        except Exception:
            return None

        if (
            goal_lat is None
            or goal_lon is None
        ):
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

    def _send_packet(
        self,
        packet: Packet,
    ) -> None:

        payload = packet.encode()

        self.redis.lpush(
            config.REDIS_RAW_TX,
            payload.hex(),
        )

    # ============================================================
    # Packet RX
    # ============================================================

    def handle_packet(
        self,
        packet: Packet,
    ) -> None:

        # --------------------------------------------------------
        # Duplicate Suppression
        # --------------------------------------------------------

        if self._is_duplicate(packet):

            print(
                f"[MESH-DUP] "
                f"type={packet.msg_type} "
                f"data={packet.data_id} "
                f"id={packet.pkt_id}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # ASK
        # --------------------------------------------------------

        if packet.msg_type == "ASK":

            if (
                isinstance(packet.data_id, str)
                and packet.data_id.startswith("O")
            ):

                self._handle_order_ask(
                    packet
                )

            else:

                self._handle_ask(
                    packet
                )

            return

        # --------------------------------------------------------
        # REPLY
        # --------------------------------------------------------

        if packet.msg_type == "REPLY":

            self._handle_reply(
                packet
            )

            return

        print(
            f"[MESH-DROP] "
            f"unknown type={packet.msg_type}",
            flush=True,
        )

    # ============================================================
    # Web Order ASK
    # ============================================================

    def _handle_order_ask(
        self,
        packet: Packet,
    ) -> None:

        match = re.fullmatch(
            r"O(\d{2}):([1-9]\d*)",
            packet.data_id,
        )

        if not match:

            print(
                f"[ORDER-DROP] "
                f"invalid data={packet.data_id!r}",
                flush=True,
            )

            return

        item_no = int(
            match.group(1)
        )

        qty = int(
            match.group(2)
        )

        if not (
            1 <= item_no <= len(ITEM_CODES)
            and 1 <= qty <= 9999
        ):

            print(
                f"[ORDER-DROP] "
                f"invalid item/qty "
                f"id={packet.pkt_id}",
                flush=True,
            )

            return

        if "-" not in packet.pkt_id:

            print(
                f"[ORDER-DROP] "
                f"invalid pkt_id={packet.pkt_id!r}",
                flush=True,
            )

            return

        origin_id = packet.pkt_id.split(
            "-",
            1,
        )[0]

        role = getattr(
            config,
            "NODE_ROLE",
            None,
        )

        # --------------------------------------------------------
        # BASE
        # --------------------------------------------------------

        if role == "BASE":

            if origin_id == str(
                config.OWN_ID
            ):

                print(
                    f"[ORDER-OWN-SKIP] "
                    f"pkt={packet.pkt_id}",
                    flush=True,
                )

                return

            self._save_sender_position(
                packet
            )

            record = {
                "pkt_id": packet.pkt_id,
                "origin_node": origin_id,
                "received_by": str(
                    config.OWN_ID
                ),
                "item_code": ITEM_CODES[
                    item_no - 1
                ],
                "item_no": item_no,
                "requested_qty": qty,
                "goal_bst": str(
                    packet.goal_bst
                ),
                "goal_bit_len": (
                    packet.goal_bit_len
                ),
                "start_bst": str(
                    packet.start_bst
                ),
                "start_bit_len": (
                    packet.start_bit_len
                ),
                "received_at": time.time(),
                "source": "LORA_ORDER_ASK",
            }

            key = (
                "lora:orders:received:"
                f"{origin_id}:{packet.pkt_id}"
            )

            serialized = json.dumps(
                record,
                ensure_ascii=False,
            )

            self.redis.set(
                key,
                serialized,
            )

            self.redis.lpush(
                "lora:orders:received",
                serialized,
            )

            self.redis.ltrim(
                "lora:orders:received",
                0,
                999,
            )

            self.redis.publish(
                "event:business:order_received",
                serialized,
            )

            print(
                f"[ORDER-RECEIVED] "
                f"pkt={packet.pkt_id} "
                f"from={origin_id} "
                f"item={record['item_code']} "
                f"qty={qty} "
                f"base={config.OWN_ID}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # RELAY
        # --------------------------------------------------------

        if role == "RELAY":

            print(
                f"[ORDER-RELAY-CHECK] "
                f"pkt={packet.pkt_id} "
                f"ttl={packet.ttl}",
                flush=True,
            )

            self._forward(
                packet
            )

            return

        print(
            f"[ORDER-SKIP] "
            f"role={role} "
            f"pkt={packet.pkt_id}",
            flush=True,
        )

    # ============================================================
    # Normal ASK
    # ============================================================

    def _handle_ask(
        self,
        packet: Packet,
    ) -> None:

        self._save_sender_position(
            packet
        )

        local = self._get_local_bst()

        is_target_area = False

        if local is not None:

            local_bst, local_bit_len = (
                local
            )

            is_target_area = is_match(
                packet.goal_bst,
                local_bst,
            )

        has_data = (
            packet.data_id
            in config.MY_DATA_IDS
        )

        # --------------------------------------------------------
        # Target node
        # --------------------------------------------------------

        if (
            is_target_area
            and has_data
            and local is not None
        ):

            local_bst, local_bit_len = (
                local
            )

            print(
                f"[ASK-DATA-FOUND] "
                f"id={packet.pkt_id} "
                f"data={packet.data_id}",
                flush=True,
            )

            reply_distance = (
                self._distance_to_bst(
                    packet.start_bst,
                    packet.start_bit_len,
                )
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

            reply_packet = (
                make_reply_packet(
                    pkt_id=packet.pkt_id,
                    goal_bst=packet.start_bst,
                    goal_bit_len=(
                        packet.start_bit_len
                    ),
                    start_bst=local_bst,
                    start_bit_len=local_bit_len,
                    data_id=packet.data_id,
                    distance=reply_distance,
                )
            )

            self.mark_sent(
                reply_packet
            )

            self._send_packet(
                reply_packet
            )

            print(
                f"[REPLY-SEND] "
                f"id={reply_packet.pkt_id} "
                f"data={reply_packet.data_id}",
                flush=True,
            )

            return

        self._forward(
            packet
        )

    # ============================================================
    # REPLY
    # ============================================================

    def _handle_reply(
        self,
        packet: Packet,
    ) -> None:

        data_id = packet.data_id

        # --------------------------------------------------------
        # REPLY(P): 提供可能数
        # --------------------------------------------------------

        if (
            isinstance(data_id, str)
            and data_id.startswith("P")
            and re.fullmatch(
                r"P\d{2}:\d+:[0-9A-Z]+",
                data_id,
            )
        ):

            self._handle_order_reply(
                packet
            )

            return

        # --------------------------------------------------------
        # NEW: REPLY(C): 受領確認
        # --------------------------------------------------------

        if (
            isinstance(data_id, str)
            and data_id.startswith("C")
            and re.fullmatch(
                r"C\d{2}:\d+:[0-9A-Z]+",
                data_id,
            )
        ):

            self._handle_order_receipt(
                packet
            )

            return

        # --------------------------------------------------------
        # REPLY(POS): 位置通知
        # --------------------------------------------------------

        if data_id == "POS":

            role = getattr(
                config,
                "NODE_ROLE",
                None,
            )

            if role == "BASE":

                self._save_mobile_position(
                    packet
                )

            elif role == "RELAY":

                self._forward_monitor_position(
                    packet
                )

            else:

                print(
                    f"[MONITOR-SKIP] "
                    f"role={role} "
                    f"id={packet.pkt_id}",
                    flush=True,
                )

            return

        # --------------------------------------------------------
        # Normal REPLY
        # --------------------------------------------------------

        origin_node_id = (
            packet.pkt_id.split(
                "-",
                1,
            )[0]
        )

        if origin_node_id == str(
            config.OWN_ID
        ):

            print(
                f"[REPLY-ARRIVED] "
                f"id={packet.pkt_id} "
                f"data={packet.data_id}",
                flush=True,
            )

            return

        self._forward(
            packet
        )

    # ============================================================
    # REPLY(P): Business Order Response
    # ============================================================

    def _handle_order_reply(
        self,
        packet: Packet,
    ) -> None:

        match = re.fullmatch(
            r"P(\d{2}):(\d+):([0-9A-Z]+)",
            packet.data_id,
        )

        if not match:

            print(
                f"[ORDER-REPLY-DROP] "
                f"invalid data={packet.data_id!r}",
                flush=True,
            )

            return

        item_no = int(
            match.group(1)
        )

        qty = int(
            match.group(2)
        )

        if not (
            1 <= item_no <= len(ITEM_CODES)
            and 0 <= qty <= 9999
        ):

            print(
                f"[ORDER-REPLY-DROP] "
                f"invalid item/qty "
                f"id={packet.pkt_id}",
                flush=True,
            )

            return

        role = getattr(
            config,
            "NODE_ROLE",
            None,
        )

        # --------------------------------------------------------
        # RELAY
        # --------------------------------------------------------

        if role == "RELAY":

            self._forward(
                packet
            )

            return

        # --------------------------------------------------------
        # BASE A
        # --------------------------------------------------------

        origin = packet.pkt_id.split(
            "-",
            1,
        )[0]

        if (
            role != "BASE"
            or origin != str(config.OWN_ID)
        ):

            print(
                f"[ORDER-REPLY-SKIP] "
                f"role={role} "
                f"origin={origin}",
                flush=True,
            )

            return

        original_raw = self.redis.get(
            f"lora:orders:outgoing:"
            f"{packet.pkt_id}"
        )

        if not original_raw:

            print(
                f"[ORDER-REPLY-DROP] "
                f"mapping missing "
                f"ask={packet.pkt_id}",
                flush=True,
            )

            return

        try:

            original = json.loads(
                original_raw
            )

            if int(
                original.get(
                    "item_no",
                    -1,
                )
            ) != item_no:

                raise ValueError(
                    "item mismatch"
                )

            requested_qty = int(
                original.get(
                    "requested_qty",
                    -1,
                )
            )

            if qty > requested_qty:

                raise ValueError(
                    "qty exceeds request"
                )

            responder_num = int(
                match.group(3),
                36,
            )

            responder = str(
                responder_num
            ).zfill(
                len(str(config.OWN_ID))
            )

        except (
            ValueError,
            TypeError,
            KeyError,
        ) as exc:

            print(
                f"[ORDER-REPLY-DROP] "
                f"ask={packet.pkt_id} "
                f"error={exc}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # Record
        # --------------------------------------------------------

        record = {
            "source": "LORA_ORDER_REPLY",
            "ask_pkt_id": packet.pkt_id,
            "order_id": original["order_id"],
            "item_code": original["item_code"],
            "requested_qty": requested_qty,
            "accepted_qty": qty,
            "responder_node": responder,
            "received_at": time.time(),
        }

        serialized = json.dumps(
            record,
            ensure_ascii=False,
        )

        key = (
            "lora:orders:reply_received:"
            f"{packet.pkt_id}"
        )

        # --------------------------------------------------------
        # NEW: 回答元BASEの位置を保存
        #
        # mesh_daemon.pyのprocess_receipt()が
        # 受領通知の宛先として利用する。
        # --------------------------------------------------------

        position_key = (
            "lora:orders:reply_position:"
            f"{packet.pkt_id}"
        )

        position_record = {
            "ask_pkt_id": packet.pkt_id,
            "responder_node": responder,
            "start_bst": str(
                packet.start_bst
            ),
            "start_bit_len": (
                packet.start_bit_len
            ),
            "received_at": time.time(),
        }

        # --------------------------------------------------------
        # 重複登録防止
        # --------------------------------------------------------

        if not self.redis.set(
            key,
            serialized,
            nx=True,
        ):

            print(
                f"[ORDER-REPLY-DUP] "
                f"ask={packet.pkt_id}",
                flush=True,
            )

            return

        # REPLY記録と位置情報を保存
        self.redis.set(
            position_key,
            json.dumps(
                position_record,
                ensure_ascii=False,
            ),
        )

        self.redis.lpush(
            "lora:orders:replies",
            serialized,
        )

        self.redis.ltrim(
            "lora:orders:replies",
            0,
            999,
        )

        print(
            f"[ORDER-REPLY-RECEIVED] "
            f"ask={packet.pkt_id} "
            f"item={record['item_code']} "
            f"accepted={qty} "
            f"responder={responder}",
            flush=True,
        )

        print(
            f"[ORDER-REPLY-POSITION] "
            f"ask={packet.pkt_id} "
            f"responder={responder} "
            f"bst={packet.start_bst}",
            flush=True,
        )

    # ============================================================
    # NEW: REPLY(C): Order Receipt Confirmation
    # ============================================================

    def _handle_order_receipt(
        self,
        packet: Packet,
    ) -> None:
        """
        発注元BASE Aが送信した受領確認を、
        受注元BASE Bで記録する。

        例:
            msg_type = REPLY
            pkt_id   = 0001-5
            data_id  = C01:2:1

        C01:2:1
            item_no = 01
            qty     = 2
            sender  = 0001

        1つのASKについて1回だけ受領確定する。
        """

        match = re.fullmatch(
            r"C(\d{2}):(\d+):([0-9A-Z]+)",
            packet.data_id,
        )

        if not match:

            print(
                f"[ORDER-RECEIPT-DROP] "
                f"invalid data={packet.data_id!r}",
                flush=True,
            )

            return

        item_no = int(
            match.group(1)
        )

        received_qty = int(
            match.group(2)
        )

        try:

            sender_num = int(
                match.group(3),
                36,
            )

            sender_node = str(
                sender_num
            ).zfill(
                len(str(config.OWN_ID))
            )

        except ValueError:

            print(
                "[ORDER-RECEIPT-DROP] "
                "invalid sender ID",
                flush=True,
            )

            return

        if not (
            1 <= item_no <= len(ITEM_CODES)
            and 0 <= received_qty <= 9999
        ):

            print(
                f"[ORDER-RECEIPT-DROP] "
                f"invalid item/qty "
                f"id={packet.pkt_id}",
                flush=True,
            )

            return

        if "-" not in packet.pkt_id:

            print(
                "[ORDER-RECEIPT-DROP] "
                "invalid ASK packet ID",
                flush=True,
            )

            return

        origin = packet.pkt_id.split(
            "-",
            1,
        )[0]

        # --------------------------------------------------------
        # 受領確認は元のASK発行元から送られる
        # --------------------------------------------------------

        if sender_node != origin:

            print(
                f"[ORDER-RECEIPT-DROP] "
                f"sender mismatch "
                f"ask={packet.pkt_id} "
                f"sender={sender_node}",
                flush=True,
            )

            return

        role = getattr(
            config,
            "NODE_ROLE",
            None,
        )

        # --------------------------------------------------------
        # RELAY: 既存の位置指向性で転送
        # --------------------------------------------------------

        if role == "RELAY":

            print(
                f"[ORDER-RECEIPT-RELAY] "
                f"ask={packet.pkt_id} "
                f"ttl={packet.ttl}",
                flush=True,
            )

            self._forward(
                packet
            )

            return

        # --------------------------------------------------------
        # BASE以外は処理しない
        # --------------------------------------------------------

        if role != "BASE":
            return

        # 自分が発注元なら受信処理しない
        if origin == str(config.OWN_ID):

            print(
                f"[ORDER-RECEIPT-OWN-SKIP] "
                f"ask={packet.pkt_id}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # 自局が元のASKを受け付けたBASEか確認
        # --------------------------------------------------------

        original_key = (
            "lora:orders:received:"
            f"{origin}:{packet.pkt_id}"
        )

        original_raw = self.redis.get(
            original_key
        )

        if not original_raw:

            print(
                f"[ORDER-RECEIPT-DROP] "
                f"original ASK not found "
                f"ask={packet.pkt_id}",
                flush=True,
            )

            return

        try:

            original = json.loads(
                original_raw
            )

            if str(
                original.get("received_by")
            ) != str(config.OWN_ID):

                raise ValueError(
                    "wrong destination BASE"
                )

            if int(
                original["item_no"]
            ) != item_no:

                raise ValueError(
                    "item mismatch"
                )

            requested_qty = int(
                original["requested_qty"]
            )

            if received_qty > requested_qty:

                raise ValueError(
                    "received_qty exceeds request"
                )

            item_code = original[
                "item_code"
            ]

        except (
            ValueError,
            TypeError,
            KeyError,
        ) as exc:

            print(
                f"[ORDER-RECEIPT-DROP] "
                f"ask={packet.pkt_id} "
                f"error={exc}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # 受領情報を記録
        # --------------------------------------------------------

        record = {
            "source": "LORA_ORDER_RECEIPT",
            "ask_pkt_id": packet.pkt_id,
            "order_id": (
                f"lora-{packet.pkt_id}"
            ),
            "origin_node": origin,
            "received_by": str(
                config.OWN_ID
            ),
            "item_code": item_code,
            "item_no": item_no,
            "requested_qty": requested_qty,
            "received_qty": received_qty,
            "received_at": time.time(),
        }

        serialized = json.dumps(
            record,
            ensure_ascii=False,
        )

        receipt_key = (
            "lora:orders:receipt_received:"
            f"{packet.pkt_id}"
        )

        # --------------------------------------------------------
        # 同じASKについて受領記録は1回のみ
        # --------------------------------------------------------

        if not self.redis.set(
            receipt_key,
            serialized,
            nx=True,
        ):

            print(
                f"[ORDER-RECEIPT-DUP] "
                f"ask={packet.pkt_id}",
                flush=True,
            )

            return

        # --------------------------------------------------------
        # Web同期用Redisリスト
        # --------------------------------------------------------

        self.redis.lpush(
            "lora:orders:receipts",
            serialized,
        )

        self.redis.ltrim(
            "lora:orders:receipts",
            0,
            999,
        )

        self.redis.publish(
            "event:business:receipt_received",
            serialized,
        )

        print(
            f"[ORDER-RECEIPT-RECEIVED] "
            f"ask={packet.pkt_id} "
            f"from={origin} "
            f"item={item_code} "
            f"received={received_qty} "
            f"base={config.OWN_ID}",
            flush=True,
        )

    # ============================================================
    # POS Forward
    # ============================================================

    def _forward_monitor_position(
        self,
        packet: Packet,
    ) -> None:

        if not packet.can_forward():

            print(
                f"[MONITOR-NO-FORWARD] "
                f"id={packet.pkt_id} "
                f"ttl={packet.ttl}",
                flush=True,
            )

            return

        forward_packet = (
            packet.forwarded()
        )

        if forward_packet is None:
            return

        try:

            self._send_packet(
                forward_packet
            )

        except Exception as exc:

            print(
                f"[MONITOR-FORWARD-ERR] "
                f"id={packet.pkt_id} "
                f"error={exc}",
                flush=True,
            )

            return

        print(
            f"[MONITOR-FORWARD] "
            f"id={forward_packet.pkt_id} "
            f"ttl={packet.ttl}"
            f"->{forward_packet.ttl}",
            flush=True,
        )

    # ============================================================
    # Directional Forward
    # ============================================================

    def _forward(
        self,
        packet: Packet,
    ) -> None:

        if not packet.can_forward():
            return

        local_distance = (
            self._distance_to_bst(
                packet.goal_bst,
                packet.goal_bit_len,
            )
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

            previous_distance = float(
                packet.distance
            )

        except (
            TypeError,
            ValueError,
        ):

            print(
                f"[DIRECTION-DROP] "
                f"id={packet.pkt_id} "
                f"reason=invalid_previous_distance",
                flush=True,
            )

            return

        if (
            not math.isfinite(previous_distance)
            or previous_distance <= 0.0
        ):

            print(
                f"[DIRECTION-DROP] "
                f"id={packet.pkt_id} "
                f"reason=invalid_previous_distance",
                flush=True,
            )

            return

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

        forward_packet = (
            packet.forwarded()
        )

        if forward_packet is None:
            return

        forward_packet.distance = (
            local_distance
        )

        self._send_packet(
            forward_packet
        )

        print(
            f"[DIRECTION-FORWARD] "
            f"type={forward_packet.msg_type} "
            f"data={forward_packet.data_id} "
            f"id={forward_packet.pkt_id} "
            f"prev={previous_distance:.2f} "
            f"local={local_distance:.2f} "
            f"ttl={forward_packet.ttl}",
            flush=True,
        )

    # ============================================================
    # Mark Sent
    # ============================================================

    def mark_sent(
        self,
        packet: Packet,
    ) -> None:
        """
        自ノードが生成したパケットを
        重複排除テーブルに登録する。

        PとCは別のキーで登録する。
        """

        self._is_duplicate(
            packet
        )

    # ============================================================
    # Save ASK Sender Position
    # ============================================================

    def _save_sender_position(
        self,
        packet: Packet,
    ) -> None:

        try:

            lon, lat, _, _ = (
                BSTIDDecoder.decode(
                    packet.start_bst,
                    packet.start_bit_len,
                )
            )

        except Exception as exc:

            print(
                f"[NODE-POS-ERR] "
                f"BST decode failed: {exc}",
                flush=True,
            )

            return

        if lat is None or lon is None:

            print(
                "[NODE-POS-ERR] "
                "lat/lon is None",
                flush=True,
            )

            return

        node_id = packet.pkt_id.split(
            "-",
            1,
        )[0]

        node_state = {
            "node_id": node_id,
            "lat": lat,
            "lon": lon,
            "bst_id_int": str(
                packet.start_bst
            ),
            "bst_bit_len": (
                packet.start_bit_len
            ),
            "updated_at": time.time(),
            "source": "ASK",
        }

        key = (
            f"lora:nodes:{node_id}"
        )

        self.redis.set(
            key,
            json.dumps(node_state),
        )

        print(
            f"[NODE-POSITION] "
            f"node={node_id} "
            f"lat={lat} "
            f"lon={lon}",
            flush=True,
        )

    # ============================================================
    # Save Mobile Position
    # ============================================================

    def _save_mobile_position(
        self,
        packet: Packet,
    ) -> None:

        node_id = packet.pkt_id.split(
            "-",
            1,
        )[0]

        if (
            not node_id
            or "-" not in packet.pkt_id
        ):

            print(
                f"[MONITOR-DROP] "
                f"invalid pkt_id={packet.pkt_id}",
                flush=True,
            )

            return

        try:

            lon, lat, _, _ = (
                BSTIDDecoder.decode(
                    packet.start_bst,
                    packet.start_bit_len,
                )
            )

            if (
                lat is None
                or lon is None
            ):
                raise ValueError(
                    "decoded coordinates missing"
                )

            lat = float(lat)
            lon = float(lon)

            if not (
                -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                raise ValueError(
                    "coordinates out of range"
                )

        except Exception as exc:

            print(
                f"[MONITOR-DROP] "
                f"id={packet.pkt_id} "
                f"error={exc}",
                flush=True,
            )

            return

        node_state = {
            "node_id": node_id,
            "lat": lat,
            "lon": lon,
            "bst_id_int": str(
                packet.start_bst
            ),
            "bst_bit_len": (
                packet.start_bit_len
            ),
            "updated_at": time.time(),
            "packet_id": packet.pkt_id,
            "source": "REPLY_POS",
        }

        prefix = getattr(
            config,
            "REDIS_NODE_PREFIX",
            "lora:nodes:",
        )

        key = (
            f"{prefix}{node_id}"
        )

        # --------------------------------------------------------
        # Receive Count
        # --------------------------------------------------------

        count_key = (
            f"lora:monitor:rx_count:{node_id}"
        )

        rx_count = self.redis.incr(
            count_key
        )

        node_state["rx_count"] = (
            rx_count
        )

        self.redis.set(
            key,
            json.dumps(node_state),
        )

        print(
            f"[MONITOR-REPLY] "
            f"node={node_id} "
            f"pkt={packet.pkt_id} "
            f"lat={lat:.8f} "
            f"lon={lon:.8f} "
            f"rx_count={rx_count}",
            flush=True,
        )
