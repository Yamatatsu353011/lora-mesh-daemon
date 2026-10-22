
# -*- coding: utf-8 -*-

import config

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
        tx_slot: int = None,
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

        # ========================================================
        # Fixed transmission slot
        # ========================================================

        # 指定がなければconfig.pyのTX_SLOTを使用
        if tx_slot is None:
            tx_slot = config.TX_SLOT

        # スロット番号の型確認
        if (
            isinstance(tx_slot, bool)
            or not isinstance(tx_slot, int)
        ):
            raise ValueError(
                "TX_SLOT must be an integer"
            )

        # スロット番号の範囲確認
        if not 0 <= tx_slot < self.num_slots:
            raise ValueError(
                f"TX_SLOT must be "
                f"0 to {self.num_slots - 1}"
            )

        self.tx_slot = tx_slot

        self.current_frame = None

        # そのframeですでに送ったか
        self.sent_frame = None


    # ============================================================
    # Frame update
    # ============================================================

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

        # 固定スロット方式：
        # フレームが変わってもtx_slotは変更しない

        print(
            f"[ALOHA] "
            f"frame={frame_no} "
            f"tx_slot={self.tx_slot}/"
            f"{self.num_slots - 1}",
            flush=True,
        )


    # ============================================================
    # Transmission permission
    # ============================================================

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


    # ============================================================
    # Mark transmitted
    # ============================================================

    def mark_transmitted(
        self,
    ) -> None:

        self.sent_frame = (
            self.current_frame
        )


    # ============================================================
    # Status
    # ============================================================

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
