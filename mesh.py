# -*- coding: utf-8 -*-
import time


class DuplicateSuppressor:
    def __init__(self, retention_sec: float = 300.0):
        self.retention_sec = retention_sec
        self._seen: dict[str, float] = {}

    def _make_key(self, msg_type: str, pkt_id: str) -> str:
        return f"{msg_type}:{pkt_id}"

    def is_duplicate(self, msg_type: str, pkt_id: str) -> bool:
        """
        初めて受信したパケットならFalse。
        すでに受信済みならTrue。
        """
        self.cleanup()

        key = self._make_key(msg_type, pkt_id)

        if key in self._seen:
            return True

        self._seen[key] = time.monotonic()
        return False

    def cleanup(self) -> None:
        """保存期間を過ぎたパケットIDを削除する。"""
        now = time.monotonic()

        expired_keys = [
            key
            for key, seen_at in self._seen.items()
            if now - seen_at > self.retention_sec
        ]

        for key in expired_keys:
            del self._seen[key]
