# -*- coding: utf-8 -*-

import time


def now_sec() -> float:
    """
    Slotted ALOHAで使用する共通時間。

    現在:
        NTP / chrony -> OS clock

    将来:
        GPS / PPS -> chrony -> OS clock

    ALOHA側は常にOS時刻だけを見る。
    """
    return time.time()


def get_frame_number(
    frame_sec: float,
) -> int:
    return int(
        now_sec() // frame_sec
    )


def get_frame_start(
    frame_no: int,
    frame_sec: float,
) -> float:
    return (
        frame_no * frame_sec
    )


def get_slot_number(
    frame_sec: float,
    slot_sec: float,
) -> int:

    now = now_sec()

    frame_no = int(
        now // frame_sec
    )

    frame_start = (
        frame_no * frame_sec
    )

    elapsed = (
        now - frame_start
    )

    return int(
        elapsed // slot_sec
    )


def get_slot_offset(
    frame_sec: float,
    slot_sec: float,
) -> float:
    """
    現在slot開始からの経過時間[s]
    """

    now = now_sec()

    frame_no = int(
        now // frame_sec
    )

    frame_start = (
        frame_no * frame_sec
    )

    elapsed = (
        now - frame_start
    )

    return (
        elapsed % slot_sec
    )
