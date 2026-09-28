# -*- coding: utf-8 -*-

import random

from time_utils import (
    now_sec,
)


class SlottedAloha:

    def __init__(
        self,
        frame_sec: float,
        slot_sec: float,
        guard_sec: float,
        enabled: bool = True,
    ):

        self.enabled = enabled

        self.frame_sec = float(
            frame_sec
        )

        self.slot_sec = float(
            slot_sec
        )

        self.guard_sec = float(
            guard_sec
        )

        if self.frame_sec <= 0:
            raise ValueError(
                "FRAME_SEC must be > 0"
            )

        if self.slot_sec <= 0:
            raise ValueError(
                "SLOT_SEC must be > 0"
            )

        slots = (
            self.frame_sec
            / self.slot_sec
        )

        self.num_slots = round(
            slots
        )

        if abs(
            slots - self.num_slots
        ) > 1e-9:
            raise ValueError(
                "FRAME_SEC must be "
                "divisible by SLOT_SEC"
            )

        if (
            self.guard_sec < 0
            or self.guard_sec * 2
            >= self.slot_sec
        ):
            raise ValueError(
                "invalid TX_GUARD_SEC"
            )

        # 各ノードで独立した乱数
        self.random = (
            random.SystemRandom()
        )

        self.current_frame = None
        self.tx_slot = None

        # そのframeですでに送ったか
        self.sent_frame = None


    def _update_frame(
        self,
        now: float,
    ) -> None:

        frame_no = int(
            now // self.frame_sec
        )

        if (
            frame_no
            == self.current_frame
        ):
            return

        self.current_frame = (
            frame_no
        )

        # Slotted ALOHA:
        # frameごとに送信slotをランダム選択
        self.tx_slot = (
            self.random.randrange(
                self.num_slots
            )
        )

        print(
            f"[ALOHA] "
            f"frame={frame_no} "
            f"tx_slot={self.tx_slot}/"
            f"{self.num_slots - 1}",
            flush=True,
        )


    def can_transmit(
        self,
    ) -> bool:
        """
        今この瞬間に送信可能ならTrue。

        1 frameにつき最大1回。
        """

        if not self.enabled:
            return True

        now = now_sec()

        self._update_frame(
            now
        )

        # このframeで送信済み
        if (
            self.sent_frame
            == self.current_frame
        ):
            return False

        frame_start = (
            self.current_frame
            * self.frame_sec
        )

        elapsed = (
            now - frame_start
        )

        current_slot = int(
            elapsed // self.slot_sec
        )

        if (
            current_slot
            != self.tx_slot
        ):
            return False

        slot_start = (
            self.tx_slot
            * self.slot_sec
        )

        offset = (
            elapsed - slot_start
        )

        # slot境界直後を避ける
        if (
            offset
            < self.guard_sec
        ):
            return False

        # 次slot直前も避ける
        if (
            offset
            >= (
                self.slot_sec
                - self.guard_sec
            )
        ):
            return False

        return True


    def mark_transmitted(
        self,
    ) -> None:

        self.sent_frame = (
            self.current_frame
        )


    def status(
        self,
    ) -> dict:

        now = now_sec()

        self._update_frame(
            now
        )

        frame_start = (
            self.current_frame
            * self.frame_sec
        )

        elapsed = (
            now - frame_start
        )

        current_slot = int(
            elapsed // self.slot_sec
        )

        return {
            "frame": self.current_frame,
            "current_slot": current_slot,
            "tx_slot": self.tx_slot,
            "num_slots": self.num_slots,
        }
