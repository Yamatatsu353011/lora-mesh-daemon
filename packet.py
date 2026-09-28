# -*- coding: utf-8 -*-

from dataclasses import dataclass, replace
from typing import Optional
import struct


DEFAULT_TTL = 10
BST_BYTES = 10
MAX_PACKET_BYTES = 50


@dataclass
class Packet:
    msg_type: str
    pkt_id: str

    goal_bst: int
    goal_bit_len: int

    start_bst: int
    start_bit_len: int

    data_id: str

    ttl: int = DEFAULT_TTL
    distance: float = 0.0


    # ============================================================
    # Binary encode
    # ============================================================

    def encode(self) -> bytes:

        if self.msg_type == "ASK":
            type_byte = 0

        elif self.msg_type == "REPLY":
            type_byte = 1

        else:
            raise ValueError(
                f"Unknown message type: {self.msg_type}"
            )


        pkt_id_bytes = self.pkt_id.encode("utf-8")
        data_id_bytes = self.data_id.encode("utf-8")


        if len(pkt_id_bytes) > 255:
            raise ValueError("pkt_id too long")

        if len(data_id_bytes) > 255:
            raise ValueError("data_id too long")


        if not 0 <= self.goal_bit_len <= 120:
            raise ValueError("invalid goal_bit_len")

        if not 0 <= self.start_bit_len <= 120:
            raise ValueError("invalid start_bit_len")


        if not 0 <= self.ttl <= 255:
            raise ValueError("ttl must be 0-255")


        packet = bytearray()


        # message type
        packet += struct.pack(
            ">B",
            type_byte
        )


        # packet ID
        packet += struct.pack(
            ">B",
            len(pkt_id_bytes)
        )

        packet += pkt_id_bytes


        # data ID
        packet += struct.pack(
            ">B",
            len(data_id_bytes)
        )

        packet += data_id_bytes


        # goal BST-ID
        packet += struct.pack(
            ">B",
            self.goal_bit_len
        )

        packet += self.goal_bst.to_bytes(
            BST_BYTES,
            byteorder="big"
        )


        # start BST-ID
        packet += struct.pack(
            ">B",
            self.start_bit_len
        )

        packet += self.start_bst.to_bytes(
            BST_BYTES,
            byteorder="big"
        )


        # TTL
        packet += struct.pack(
            ">B",
            self.ttl
        )


        # previous-node -> goal distance
        packet += struct.pack(
            ">f",
            self.distance
        )


        if len(packet) > MAX_PACKET_BYTES:
            raise ValueError(
                f"Packet too large: "
                f"{len(packet)} bytes"
            )


        return bytes(packet)


    # ============================================================
    # Binary decode
    # ============================================================

    @classmethod
    def decode(
        cls,
        data: bytes
    ) -> Optional["Packet"]:

        try:

            offset = 0


            # ----------------------------
            # type
            # ----------------------------

            type_byte = data[offset]
            offset += 1

            if type_byte == 0:
                msg_type = "ASK"

            elif type_byte == 1:
                msg_type = "REPLY"

            else:
                return None


            # ----------------------------
            # packet ID
            # ----------------------------

            pkt_id_len = data[offset]
            offset += 1

            pkt_id = data[
                offset:
                offset + pkt_id_len
            ].decode("utf-8")

            offset += pkt_id_len


            # ----------------------------
            # data ID
            # ----------------------------

            data_id_len = data[offset]
            offset += 1

            data_id = data[
                offset:
                offset + data_id_len
            ].decode("utf-8")

            offset += data_id_len


            # ----------------------------
            # goal BST
            # ----------------------------

            goal_bit_len = data[offset]
            offset += 1

            goal_bst = int.from_bytes(
                data[
                    offset:
                    offset + BST_BYTES
                ],
                byteorder="big"
            )

            offset += BST_BYTES


            # ----------------------------
            # start BST
            # ----------------------------

            start_bit_len = data[offset]
            offset += 1

            start_bst = int.from_bytes(
                data[
                    offset:
                    offset + BST_BYTES
                ],
                byteorder="big"
            )

            offset += BST_BYTES


            # ----------------------------
            # TTL
            # ----------------------------

            ttl = data[offset]
            offset += 1


            # ----------------------------
            # distance
            # ----------------------------

            distance = struct.unpack_from(
                ">f",
                data,
                offset
            )[0]

            offset += 4


            # 余計なbyteがあった場合も異常扱い
            if offset != len(data):
                return None


            return cls(
                msg_type=msg_type,
                pkt_id=pkt_id,
                goal_bst=goal_bst,
                goal_bit_len=goal_bit_len,
                start_bst=start_bst,
                start_bit_len=start_bit_len,
                data_id=data_id,
                ttl=ttl,
                distance=distance,
            )


        except (
            ValueError,
            IndexError,
            UnicodeDecodeError,
            struct.error,
        ):
            return None


    # ============================================================
    # Forward
    # ============================================================

    def can_forward(self) -> bool:
        return self.ttl > 0


    def forwarded(
        self,
        distance: Optional[float] = None,
    ) -> Optional["Packet"]:

        if not self.can_forward():
            return None


        new_distance = (
            self.distance
            if distance is None
            else distance
        )


        return replace(
            self,
            ttl=self.ttl - 1,
            distance=new_distance,
        )


# ================================================================
# Packet constructors
# ================================================================

def make_ask_packet(
    pkt_id: str,
    goal_bst: int,
    goal_bit_len: int,
    start_bst: int,
    start_bit_len: int,
    data_id: str,
    ttl: int = DEFAULT_TTL,
    distance: float = 0.0,
) -> Packet:

    return Packet(
        msg_type="ASK",
        pkt_id=pkt_id,

        goal_bst=goal_bst,
        goal_bit_len=goal_bit_len,

        start_bst=start_bst,
        start_bit_len=start_bit_len,

        data_id=data_id,

        ttl=ttl,
        distance=distance,
    )


def make_reply_packet(
    pkt_id: str,
    goal_bst: int,
    goal_bit_len: int,
    start_bst: int,
    start_bit_len: int,
    data_id: str,
    ttl: int = DEFAULT_TTL,
    distance: float = 0.0,
) -> Packet:

    return Packet(
        msg_type="REPLY",
        pkt_id=pkt_id,

        goal_bst=goal_bst,
        goal_bit_len=goal_bit_len,

        start_bst=start_bst,
        start_bit_len=start_bit_len,

        data_id=data_id,

        ttl=ttl,
        distance=distance,
    )