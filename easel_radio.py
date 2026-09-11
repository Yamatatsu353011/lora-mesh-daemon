# -*- coding: utf-8 -*-

import time
from collections import deque
from typing import Optional

import serial

import config


class EaselRadio:
    """
    ES920LR serial driver.

    Supports:
      - processor/configuration mode
      - ASCII payload mode
      - BINARY payload mode
      - Binary format: [length: 1 byte][payload]
    """

    MAX_BINARY_PAYLOAD = 50

    def __init__(
        self,
        port: str = config.SERIAL_PORT,
        baudrate: int = config.BAUDRATE,
        timeout: float = config.SERIAL_TIMEOUT_SEC,
        debug: bool = True,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.debug = debug

        self.ser: Optional[serial.Serial] = None

        # True after format 2 is applied
        self.binary_mode = False

        # If an RF packet arrives while waiting for TX response,
        # temporarily keep it here.
        self._pending_binary_frames = deque()

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------

    def log(self, msg: str) -> None:
        if self.debug:
            print(msg, flush=True)

    # ------------------------------------------------------------
    # Serial open / close
    # ------------------------------------------------------------

    def open(self) -> None:
        self.log(
            f"[RADIO] opening {self.port} "
            f"baud={self.baudrate}"
        )

        self.ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
        )

        try:
            self.ser.setDTR(False)
            self.ser.setRTS(False)
        except Exception:
            pass

        # ES920LR起動待ち
        time.sleep(2.0)

        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

        self.log("[RADIO] opened")

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
            self.ser = None

        self.binary_mode = False
        self._pending_binary_frames.clear()

    # ------------------------------------------------------------
    # Common read helpers
    # ------------------------------------------------------------

    def _read_exact(self, size: int) -> bytes:
        """
        指定されたbyte数を読む。
        """
        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        data = bytearray()

        while len(data) < size:
            chunk = self.ser.read(size - len(data))

            if not chunk:
                break

            data.extend(chunk)

        return bytes(data)

    # ------------------------------------------------------------
    # Configuration response
    # ------------------------------------------------------------

    def _read_config_responses(
        self,
        duration_sec: float = 0.8,
    ) -> list[str]:
        """
        コンフィグレーション応答を読む。

        ASCII:
            OK\\r\\n

        BINARY:
            [length][OK]

        の両方に対応する。
        """

        if self.ser is None:
            return []

        responses = []

        end_time = time.time() + duration_sec

        while time.time() < end_time:

            first = self.ser.read(1)

            if not first:
                continue

            first_value = first[0]

            # -------------------------
            # Binary response
            #
            # 先頭byteが出力長
            # 通常 OK = 2
            # -------------------------
            if first_value <= 50:

                payload = self._read_exact(first_value)

                if len(payload) != first_value:
                    self.log(
                        "[INIT-RX-BIN-ERR] "
                        f"expected={first_value} "
                        f"actual={len(payload)}"
                    )
                    continue

                text = payload.decode(
                    "ascii",
                    errors="ignore"
                ).strip()

                if text:
                    responses.append(text)
                    self.log(
                        f"[INIT-RX-BIN] {text}"
                    )

            # -------------------------
            # ASCII response
            # -------------------------
            else:

                rest = self.ser.readline()

                raw = first + rest

                text = raw.decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                if text:
                    responses.append(text)
                    self.log(
                        f"[INIT-RX] {text}"
                    )

        return responses

    # ------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------

    def send_cmd(
        self,
        cmd: str,
        wait_sec: float = 0.3,
    ) -> list[str]:

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        msg = cmd + "\r\n"

        self.log(
            f"[INIT-TX] {repr(msg)}"
        )

        self.ser.write(
            msg.encode(
                "ascii",
                errors="ignore"
            )
        )

        self.ser.flush()

        time.sleep(wait_sec)

        return self._read_config_responses(
            duration_sec=0.5
        )

    def configure_from_config(self) -> None:
        """
        config.EASEL_CONFIG_COMMANDSを適用する。

        formatコマンドだけ最後に実行する。
        """

        self.log("[RADIO] configure ES920LR")

        # Processor modeへ
        self.send_cmd("2")

        format_value = "1"

        # --------------------------------
        # format以外を先に設定
        # --------------------------------
        for cmd, value in config.EASEL_CONFIG_COMMANDS:

            if cmd.lower() == "format":
                format_value = str(value)
                continue

            self.send_cmd(
                f"{cmd} {value}"
            )

        # --------------------------------
        # formatを最後に設定
        # --------------------------------
        self.send_cmd(
            f"format {format_value}"
        )

        if format_value == "2":
            self.binary_mode = True
            self.log(
                "[RADIO] BINARY format selected"
            )

        else:
            self.binary_mode = False
            self.log(
                "[RADIO] ASCII format selected"
            )

        # --------------------------------
        # Operation modeへ
        # --------------------------------
        self.send_cmd(
            "start",
            wait_sec=0.5
        )

        self.log("[RADIO] ready")

    # ============================================================
    # ASCII MODE
    # ============================================================

    def read_line(self) -> Optional[str]:
        """
        ASCII format用。
        """

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        if self.binary_mode:
            raise RuntimeError(
                "Radio is in BINARY mode. "
                "Use read_binary_payload()."
            )

        raw = self.ser.readline()

        if not raw:
            return None

        line = raw.decode(
            "utf-8",
            errors="ignore"
        ).strip()

        if not line:
            return None

        upper = line.upper()

        if upper == "OK":
            self.log(f"[MODEM] {line}")
            return None

        if upper.startswith("NG"):
            self.log(f"[MODEM] {line}")
            return None

        if "SELECT MODE" in upper:
            self.log(f"[MODEM] {line}")
            return None

        self.log(
            f"[RX-LINE] {line}"
        )

        return line

    def write_line(self, line: str) -> None:

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        if self.binary_mode:
            raise RuntimeError(
                "Radio is in BINARY mode. "
                "Use send_binary_payload()."
            )

        msg = line + "\r\n"

        self.ser.write(
            msg.encode(
                "utf-8",
                errors="ignore"
            )
        )

        self.ser.flush()

        self.log(
            f"[TX] {line}"
        )

    def send_payload(
        self,
        payload: str,
        max_len: int = config.MAX_TX_LINE_LEN,
    ) -> bool:

        if self.binary_mode:
            self.log(
                "[TX-DROP] Radio is BINARY mode. "
                "Use send_binary_payload()."
            )
            return False

        if payload is None:
            self.log(
                "[TX-DROP] payload is None"
            )
            return False

        if len(payload) > max_len:
            self.log(
                f"[TX-DROP] too long "
                f"len={len(payload)} "
                f"max={max_len}"
            )
            return False

        self.write_line(payload)

        return True

    # ============================================================
    # BINARY MODE
    # ============================================================

    def _read_binary_frame_raw(
        self,
        timeout_sec: float = 1.0,
    ) -> Optional[bytes]:
        """
        Binary formatの1フレームを読む。

            [length:1byte]
            [data:length bytes]
        """

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        end_time = time.time() + timeout_sec

        while time.time() < end_time:

            length_raw = self.ser.read(1)

            if not length_raw:
                continue

            length = length_raw[0]

            # RF payloadは最大50byte。
            # OK / NGレスポンスも十分小さい。
            if length > 50:
                self.log(
                    "[RX-BIN-ERR] "
                    f"invalid length={length}"
                )

                # 不正データとして捨てる
                continue

            payload = self._read_exact(length)

            if len(payload) != length:
                self.log(
                    "[RX-BIN-ERR] "
                    f"short read "
                    f"expected={length} "
                    f"actual={len(payload)}"
                )
                return None

            return payload

        return None

    def read_binary_payload(
        self,
        timeout_sec: float = 1.0,
    ) -> Optional[bytes]:
        """
        LoRaから受信したBinary payloadを返す。

        ES920LR自身の
          OK
          NG xxx
        は除外する。
        """

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        if not self.binary_mode:
            raise RuntimeError(
                "Radio is not in BINARY mode."
            )

        end_time = time.time() + timeout_sec

        while time.time() < end_time:

            # TX応答待ち中に届いたRFデータがあれば先に使う
            if self._pending_binary_frames:
                payload = (
                    self._pending_binary_frames.popleft()
                )
            else:
                remaining = max(
                    0.01,
                    end_time - time.time()
                )

                payload = self._read_binary_frame_raw(
                    timeout_sec=remaining
                )

            if payload is None:
                return None

            # -------------------------
            # Modem response
            # -------------------------

            if payload == b"OK":
                self.log("[MODEM-BIN] OK")
                continue

            if payload.startswith(b"NG"):
                text = payload.decode(
                    "ascii",
                    errors="ignore"
                )

                self.log(
                    f"[MODEM-BIN] {text}"
                )

                continue

            # -------------------------
            # Actual RF payload
            # -------------------------

            self.log(
                f"[RX-BIN] "
                f"len={len(payload)} "
                f"hex={payload.hex()}"
            )

            return payload

        return None

    def send_binary_payload(
        self,
        payload: bytes,
        wait_response: bool = True,
        response_timeout_sec: float = 2.0,
    ) -> bool:
        """
        BINARY formatで送信する。

        UART:
            [1byte length][payload]

        CRLFは付けない。
        """

        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        if not self.binary_mode:
            self.log(
                "[TX-BIN-DROP] "
                "Radio is not in BINARY mode"
            )
            return False

        if payload is None:
            self.log(
                "[TX-BIN-DROP] payload is None"
            )
            return False

        if not isinstance(
            payload,
            (bytes, bytearray)
        ):
            raise TypeError(
                "payload must be bytes or bytearray"
            )

        payload = bytes(payload)

        if len(payload) > self.MAX_BINARY_PAYLOAD:
            self.log(
                "[TX-BIN-DROP] "
                f"too long len={len(payload)} "
                f"max={self.MAX_BINARY_PAYLOAD}"
            )
            return False

        # --------------------------------
        # ES920LR Binary Payload format
        #
        # 1byte length + payload
        # --------------------------------

        frame = (
            bytes([len(payload)])
            + payload
        )

        self.ser.write(frame)
        self.ser.flush()

        self.log(
            f"[TX-BIN] "
            f"len={len(payload)} "
            f"hex={payload.hex()}"
        )

        if not wait_response:
            return True

        # --------------------------------
        # ES920LR送信結果
        # OK / NGを待つ
        # --------------------------------

        end_time = (
            time.time()
            + response_timeout_sec
        )

        while time.time() < end_time:

            remaining = max(
                0.01,
                end_time - time.time()
            )

            response = self._read_binary_frame_raw(
                timeout_sec=remaining
            )

            if response is None:
                break

            if response == b"OK":
                self.log(
                    "[TX-BIN-RESULT] OK"
                )
                return True

            if response.startswith(b"NG"):

                text = response.decode(
                    "ascii",
                    errors="ignore"
                )

                self.log(
                    f"[TX-BIN-RESULT] {text}"
                )

                return False

            # OK/NGではない
            # → RF受信データの可能性があるので捨てない
            self._pending_binary_frames.append(
                response
            )

            self.log(
                "[TX-BIN] "
                "received RF frame while "
                "waiting for TX response"
            )

        self.log(
            "[TX-BIN-RESULT] "
            "response timeout"
        )

        return False