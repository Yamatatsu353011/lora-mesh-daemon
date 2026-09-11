# -*- coding: utf-8 -*-

import struct


BST_BYTES = 15


def encode_packet(
    msg_type: str,
    pkt_id: str,
    goal_bst: int,
    start_bst: int,
    ttl: int,
    distance: float,
) -> bytes:

    if msg_type == "ASK":
        type_byte = 0
    elif msg_type == "REPLY":
        type_byte = 1
    else:
        raise ValueError("msg_type must be ASK or REPLY")

    pkt_id_bytes = pkt_id.encode("utf-8")

    if len(pkt_id_bytes) > 255:
        raise ValueError("pkt_id too long")

    if not 0 <= goal_bst < (1 << (BST_BYTES * 8)):
        raise ValueError("goal_bst does not fit in 15 bytes")

    if not 0 <= start_bst < (1 << (BST_BYTES * 8)):
        raise ValueError("start_bst does not fit in 15 bytes")

    if not 0 <= ttl <= 255:
        raise ValueError("ttl must be 0-255")

    packet = bytearray()

    packet += struct.pack(">B", type_byte)
    packet += struct.pack(">B", len(pkt_id_bytes))
    packet += pkt_id_bytes

    packet += goal_bst.to_bytes(
        BST_BYTES,
        byteorder="big"
    )

    packet += start_bst.to_bytes(
        BST_BYTES,
        byteorder="big"
    )

    packet += struct.pack(">B", ttl)
    packet += struct.pack(">f", distance)

    if len(packet) > 50:
        raise ValueError(
            f"Packet too large: {len(packet)} bytes"
        )

    return bytes(packet)


def decode_packet(data: bytes) -> dict:

    if len(data) < 36:
        raise ValueError(
            f"Packet too short: {len(data)} bytes"
        )

    offset = 0

    # A/R
    type_byte = data[offset]
    offset += 1

    if type_byte == 0:
        msg_type = "ASK"
    elif type_byte == 1:
        msg_type = "REPLY"
    else:
        raise ValueError(
            f"Unknown packet type: {type_byte}"
        )

    # packet-id length
    pkt_id_len = data[offset]
    offset += 1

    expected_len = (
        1                   # type
        + 1                 # pkt id length
        + pkt_id_len
        + BST_BYTES         # goal
        + BST_BYTES         # start
        + 1                 # TTL
        + 4                 # distance
    )

    if len(data) != expected_len:
        raise ValueError(
            f"Invalid packet length: "
            f"actual={len(data)}, expected={expected_len}"
        )

    # packet-id
    pkt_id = data[
        offset:offset + pkt_id_len
    ].decode("utf-8")

    offset += pkt_id_len

    # goal BST
    goal_bst = int.from_bytes(
        data[offset:offset + BST_BYTES],
        byteorder="big"
    )

    offset += BST_BYTES

    # start BST
    start_bst = int.from_bytes(
        data[offset:offset + BST_BYTES],
        byteorder="big"
    )

    offset += BST_BYTES

    # TTL
    ttl = data[offset]
    offset += 1

    # distance
    distance = struct.unpack_from(
        ">f",
        data,
        offset
    )[0]

    return {
        "msg_type": msg_type,
        "pkt_id": pkt_id,
        "goal_bst": goal_bst,
        "start_bst": start_bst,
        "ttl": ttl,
        "distance": distance,
    }