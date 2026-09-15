# -*- coding: utf-8 -*-

import json
import math
import time

import pynmea2
import redis
import serial

import config

from bst_id.encoder import BSTIDEncoder
from packet import make_ask_packet


# ============================================================
# Distance
# ============================================================

def haversine_distance_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    2地点間の距離[m]
    """

    r = 6371000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return r * c


# ============================================================
# GPS
# ============================================================

def read_gps_fix(gps):
    """
    有効なGGAを受け取るまで待つ。
    """

    while True:

        line = gps.readline().decode(
            "ascii",
            errors="ignore"
        ).strip()

        if not (
            line.startswith("$GNGGA")
            or line.startswith("$GPGGA")
        ):
            continue

        try:
            msg = pynmea2.parse(line)

        except pynmea2.ParseError:
            continue

        # GPS Fixなし
        if int(msg.gps_qual or 0) == 0:
            continue

        lat = msg.latitude
        lon = msg.longitude

        return lat, lon


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # Redis
    # --------------------------------------------------------

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )


    # --------------------------------------------------------
    # GPS
    # --------------------------------------------------------

    gps = serial.Serial(
        config.GPS_SERIAL_PORT,
        config.GPS_BAUDRATE,
        timeout=1,
    )


    # --------------------------------------------------------
    # ASK destination
    # --------------------------------------------------------
    #
    # 目標地点を経度・緯度で入力
    #
    # 例:
    #   longitude = 139.939698
    #   latitude  = 37.521844
    #
    # --------------------------------------------------------

    goal_lon = float(
        input("Goal longitude: ")
    )

    goal_lat = float(
        input("Goal latitude : ")
    )


    # --------------------------------------------------------
    # Goal GPS -> BST-ID
    # --------------------------------------------------------

    goal_bst, goal_bit_len = (
        BSTIDEncoder.encode(
            goal_lon,
            goal_lat,
            None,
            None,
            config.BST_ZX,
            config.BST_ZY,
            0,
            0,
        )
    )


    # --------------------------------------------------------
    # Data ID
    # --------------------------------------------------------

    data_id = input(
        "Data ID: "
    ).strip()


    # --------------------------------------------------------
    # Goal information
    # --------------------------------------------------------

    print()
    print("===== Goal =====")

    print(
        "longitude   :",
        goal_lon,
    )

    print(
        "latitude    :",
        goal_lat,
    )

    print(
        "goal_bst    :",
        goal_bst,
    )

    print(
        "goal_bit_len:",
        goal_bit_len,
    )

    print(
        "data_id     :",
        data_id,
    )

    print()

    print("[ASK-LOOP] started")


    counter = 1


    try:

        while True:

            # ====================================================
            # Current GPS
            # ====================================================

            lat, lon = read_gps_fix(gps)


            # ====================================================
            # Current GPS -> BST-ID
            # ====================================================

            start_bst, start_bit_len = (
                BSTIDEncoder.encode(
                    lon,
                    lat,
                    None,
                    None,
                    config.BST_ZX,
                    config.BST_ZY,
                    0,
                    0,
                )
            )


            # ====================================================
            # Current node -> Goal distance
            # ====================================================

            distance = haversine_distance_m(
                lat,
                lon,
                goal_lat,
                goal_lon,
            )


            # ====================================================
            # Packet ID
            #
            # BST-IDをPacket IDに使うと長すぎるため
            # LoRaモジュールのOWN_IDを使用
            # ====================================================

            pkt_id = (
                f"{config.OWN_ID}-{counter}"
            )


            # ====================================================
            # Packet
            # ====================================================

            packet = make_ask_packet(
                pkt_id=pkt_id,

                goal_bst=goal_bst,
                goal_bit_len=goal_bit_len,

                start_bst=start_bst,
                start_bit_len=start_bit_len,

                data_id=data_id,

                ttl=10,
                distance=distance,
            )


            # ====================================================
            # Binary
            # ====================================================

            payload = packet.encode()

            # RedisではBinaryを直接扱わずHEX文字列にする
            payload_hex = payload.hex()


            # ====================================================
            # Local packet notification
            # ====================================================

            r.publish(
                config.REDIS_EVENT,
                json.dumps({
                    "event": "local_packet",
                    "payload_hex": payload_hex,
                })
            )


            # ====================================================
            # RAW TX queue
            # ====================================================

            r.lpush(
                config.REDIS_RAW_TX,
                payload_hex,
            )


            # ====================================================
            # Log
            # ====================================================

            print()
            print("===== ASK SEND =====")

            print(
                "id        :",
                pkt_id,
            )

            print(
                "GPS lat   :",
                lat,
            )

            print(
                "GPS lon   :",
                lon,
            )

            print(
                "start_bst :",
                start_bst,
            )

            print(
                "start_bits:",
                start_bit_len,
            )

            print(
                "goal_bst  :",
                goal_bst,
            )

            print(
                "goal_bits :",
                goal_bit_len,
            )

            print(
                "distance  :",
                f"{distance:.2f} m",
            )

            print(
                "size      :",
                len(payload),
                "bytes",
            )

            print(
                "hex       :",
                payload_hex,
            )


            # ====================================================
            # Counter
            # ====================================================

            counter += 1

            # Packet IDが長くなりすぎないようにする
            if counter > 9999:
                counter = 1


            # ====================================================
            # Interval
            # ====================================================

            time.sleep(3)


    except KeyboardInterrupt:

        print(
            "\n[ASK-LOOP] stopped"
        )


    finally:

        gps.close()


if __name__ == "__main__":
    main()
