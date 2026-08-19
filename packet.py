# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Optional
DEFAULT_TTL = 10

@dataclass
class Packet:
    msg_type: str
    pkt_id: str
    target_bst: int
    data_id: str
    source_bst: int = -1
    responder_bst: int = -1
    ttl: int = DEFAULT_TTL

    def encode(self) -> str:
        """PacketをLoRa送信用の文字列へ変換する。"""
        if self.msg_type == "ASK":
            return (
                f"A,{self.pkt_id},{self.target_bst},"
                f"{self.data_id},{self.source_bst},{self.ttl}"
            )

        if self.msg_type == "REPLY":
            return (
                f"R,{self.pkt_id},{self.target_bst},"
                f"{self.data_id},{self.responder_bst},{self.ttl}"
            )

        raise ValueError(f"Unknown message type: {self.msg_type}")

    @classmethod
    def decode(cls, line: str) -> Optional["Packet"]:
        """LoRaで受信した文字列をPacketへ変換する。"""
        try:
            parts = [part.strip() for part in line.split(",")]

            if len(parts) < 5:
                return None

            ttl = int(parts[5]) if len(parts) >= 6 else DEFAULT_TTL

            if parts[0] == "A":
                return cls(
                    msg_type="ASK",
                    pkt_id=parts[1],
                    target_bst=int(parts[2]),
                    data_id=parts[3],
                    source_bst=int(parts[4]),
                    ttl = ttl,
                )

            if parts[0] == "R":
                return cls(
                    msg_type="REPLY",
                    pkt_id=parts[1],
                    target_bst=int(parts[2]),
                    data_id=parts[3],
                    responder_bst=int(parts[4]),
                    ttl = ttl,
                )

            return None

        except (ValueError, IndexError):
            return None

    def can_forward(self) -> bool:
        """
        TTLが残っている場合のみ中継可能。
        """
        return self.ttl > 0

    def forwarded(self) -> Optional["Packet"]:
        """
        TTLを1減らした中継用Packetを生成する。
        元のPacket自体は変更しない。
        """

        if not self.can_forward():
            return None

   


def make_ask_packet(
    pkt_id: str,
    target_bst: int,
    data_id: str,
    source_bst: int,
    ttl: int = DEFAULT_TTL,
) -> Packet:
    return Packet(
        msg_type="ASK",
        pkt_id=pkt_id,
        target_bst=target_bst,
        data_id=data_id,
        source_bst=source_bst,
        ttl = ttl,
    )


def make_reply_packet(
    pkt_id: str,
    target_bst: int,
    data_id: str,
    responder_bst: int,
    ttl: int = DEFAULT_TTL,
) -> Packet:
    return Packet(
        msg_type="REPLY",
        pkt_id=pkt_id,
        target_bst=target_bst,
        data_id=data_id,
        responder_bst=responder_bst,
        ttl = ttl,
    )

