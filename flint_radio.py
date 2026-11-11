
# -*- coding: utf-8 -*-
"""FLINT USB / CLEALINK E220-900T22S(JP) v2.0 radio adapter.

The module must already be configured in Mode 3 using FLINT's web utility.
Runtime Mode 0, fixed destination mode, channel 0, Strict Mode OFF.
REG3 bit7 (append RSSI byte) must match FLINT_RSSI_BYTE config.

This adapter creates an in-band frame, because the E220 UART output is a
byte stream without packet delimiters in v1-compatible Mode 0.
On the wire: MAGIC(2) + ver(1) + immediate-hop-source(2) + local-tx-seq(2)
           + payload-size(1) + payload(N) + CRC16(2).
UART TX additionally requires E220 dest(2) + channel(1) prefix.
UART RX additionally contains an RSSI(1) suffix if REG3 bit7 is set.
"""
from __future__ import annotations

import binascii
import struct
import time
from typing import Optional

import serial
import config


class FlintRadio:
    MAGIC = b"\xA5\x5A"
    VERSION = 1
    HEADER = struct.Struct(">2sBHHB")
    CRC = struct.Struct(">H")
    E220_MAX_UART_TX = 200
    E220_DEST_BYTES = 3
    ENVELOPE_OVERHEAD = HEADER.size + CRC.size  # 10
    MAX_BINARY_PAYLOAD = E220_MAX_UART_TX - E220_DEST_BYTES - ENVELOPE_OVERHEAD  # 187
    MAX_RX_BUFFER = 4096

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: Optional[int] = None,
        timeout: Optional[float] = None,
        debug: bool = True,
        *,
        hop_id: Optional[int] = None,
        channel: Optional[int] = None,
        rssi_appended: Optional[bool] = None,
    ):
        self.port = port or config.SERIAL_PORT
        self.baudrate = int(baudrate if baudrate is not None else config.BAUDRATE)
        self.timeout = float(timeout if timeout is not None else config.SERIAL_TIMEOUT_SEC)
        self.debug = debug
        raw_hop_id = hop_id if hop_id is not None else getattr(config, "FLINT_HOP_ID", config.OWN_ID)
        self.hop_id = int(raw_hop_id, 16) if isinstance(raw_hop_id, str) else int(raw_hop_id)
        if not (0 <= self.hop_id <= 65535):
            raise ValueError("FLINT_HOP_ID must be a 16-bit ID")
        self.channel = int(channel if channel is not None else getattr(config, "FLINT_CHANNEL", 0))
        if not (0 <= self.channel <= 30):
            raise ValueError("FLINT_CHANNEL must be 0..30 for BW500kHz")
        self.rssi_appended = bool(rssi_appended if rssi_appended is not None else getattr(config, "FLINT_RSSI_BYTE", True))
        self.ser: Optional[serial.Serial] = None
        self.binary_mode = True
        self._rx_buffer = bytearray()
        self._tx_seq = 0
        self.last_rx_rssi_dbm: Optional[int] = None
        self.last_rx_hop_id: Optional[str] = None
        self.last_rx_hop_seq: Optional[int] = None
        self.stats = {"rx_frames": 0, "rx_crc_errors": 0, "rx_bad_headers": 0, "rx_skipped_bytes": 0, "tx_uart_frames": 0}

    def log(self, message: str) -> None:
        if self.debug:
            print(message, flush=True)

    def open(self) -> None:
        self.log(f"[FLINT] opening {self.port}, baud={self.baudrate}")
        self.ser = serial.Serial(
            self.port, self.baudrate, timeout=self.timeout, write_timeout=1.0,
            rtscts=False, dsrdtr=False,
        )
        # For ordinary FLINT USB boards leave J3 OPEN (LoRa always powered).
        # Never rely on these RTS/DTR values to power a shorted J3 board.
        try:
            self.ser.setRTS(False)
            self.ser.setDTR(False)
        except Exception:
            pass
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        self._rx_buffer.clear()
        self.log("[FLINT] opened; hardware settings must have been applied beforehand")

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
            self.ser = None
        self._rx_buffer.clear()

    def configure_from_config(self) -> None:
        """Compatibility with EaselRadio; E220 register writes are NOT performed.

        The FLINT USB hardware's physical DIP switch must be OFF/OFF (Mode 0).
        Configure SF/BW/UART/CH/fixed mode/RSSI beforehand via Mode 3.
        """
        self.log(
            f"[FLINT] Mode 0, fixed TX, CH={self.channel}, RSSI appended={self.rssi_appended}. "
            "Verify actual module registers with FLINT's configuration tool."
        )

    @classmethod
    def _pack_air_frame(cls, hop_id: int, seq: int, payload: bytes) -> bytes:
        if not (1 <= len(payload) <= cls.MAX_BINARY_PAYLOAD):
            raise ValueError(f"FLINT payload must have 1..{cls.MAX_BINARY_PAYLOAD} bytes")
        data = cls.HEADER.pack(cls.MAGIC, cls.VERSION, hop_id, seq, len(payload)) + payload
        return data + cls.CRC.pack(binascii.crc_hqx(data, 0xFFFF))

    def send_binary_payload(
        self,
        payload: bytes,
        wait_response: bool = True,
        response_timeout_sec: float = 2.0,
    ) -> bool:
        """Queue a frame into E220 UART. Returns USB write success, NOT RF ACK.

        E220 Mode 0 does not return the EASEL module's OK/NG response.
        wait_response/response_timeout_sec are accepted for API compatibility.
        """
        if self.ser is None:
            raise RuntimeError("FLINT serial port not open")
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        payload = bytes(payload)
        if not (1 <= len(payload) <= self.MAX_BINARY_PAYLOAD):
            self.log(f"[FLINT-TX-DROP] payload={len(payload)} max={self.MAX_BINARY_PAYLOAD}")
            return False
        frame = self._pack_air_frame(self.hop_id, self._tx_seq, payload)
        uart = b"\xFF\xFF" + bytes([self.channel]) + frame
        assert len(uart) <= self.E220_MAX_UART_TX
        n = self.ser.write(uart)
        self.ser.flush()
        if n != len(uart):
            self.log(f"[FLINT-TX-ERR] USB wrote only {n}/{len(uart)} bytes")
            return False
        self.stats["tx_uart_frames"] += 1
        self.log(f"[FLINT-TX] hop={self.hop_id:04X} seq={self._tx_seq} bytes={len(payload)}")
        self._tx_seq = (self._tx_seq + 1) & 0xFFFF
        return True

    def _drop_prefix(self, n: int) -> None:
        del self._rx_buffer[:n]
        self.stats["rx_skipped_bytes"] += n

    def _parse_buffer(self) -> Optional[bytes]:
        """Extract one validated radio payload and its per-hop metadata."""
        buf = self._rx_buffer
        while buf:
            marker = buf.find(self.MAGIC)
            if marker == -1:
                # Preserve 0xA5 if it is the first half of the next MAGIC.
                keep = 1 if buf[-1:] == self.MAGIC[:1] else 0
                self._drop_prefix(len(buf) - keep)
                return None
            if marker:
                self._drop_prefix(marker)
            if len(buf) < self.HEADER.size:
                return None
            magic, version, source, sequence, length = self.HEADER.unpack_from(buf)
            if magic != self.MAGIC or version != self.VERSION or length == 0 or length > self.MAX_BINARY_PAYLOAD:
                self.stats["rx_bad_headers"] += 1
                self._drop_prefix(1)
                continue
            without_rssi = self.HEADER.size + length + self.CRC.size
            total = without_rssi + int(self.rssi_appended)
            if len(buf) < total:
                return None
            csum = self.CRC.unpack_from(buf, without_rssi - self.CRC.size)[0]
            actual = binascii.crc_hqx(buf[:without_rssi - self.CRC.size], 0xFFFF)
            if csum != actual:
                self.stats["rx_crc_errors"] += 1
                self._drop_prefix(1)
                continue
            payload = bytes(buf[self.HEADER.size:self.HEADER.size + length])
            self.last_rx_hop_id = f"{source:04X}"
            self.last_rx_hop_seq = sequence
            self.last_rx_rssi_dbm = (buf[without_rssi] - 256) if self.rssi_appended else None
            del buf[:total]
            self.stats["rx_frames"] += 1
            self.log(f"[FLINT-RX] hop={source:04X} seq={sequence} bytes={len(payload)} rssi={self.last_rx_rssi_dbm}")
            return payload
        return None

    def read_binary_payload(self, timeout_sec: float = 1.0) -> Optional[bytes]:
        if self.ser is None:
            raise RuntimeError("FLINT serial port not open")
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while True:
            result = self._parse_buffer()
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            # Reuses serial timeout, set to 10ms by raw_daemon.py.
            count = min(512, max(1, self.ser.in_waiting))
            chunk = self.ser.read(count)
            if chunk:
                self._rx_buffer.extend(chunk)
                if len(self._rx_buffer) > self.MAX_RX_BUFFER:
                    self._drop_prefix(len(self._rx_buffer) - self.MAX_RX_BUFFER)
