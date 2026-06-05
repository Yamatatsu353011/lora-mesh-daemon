# -*- coding: utf-8 -*-
import time
from typing import Optional

import serial

import config


class EaselRadio:
    """
    Minimal ES920LR serial driver.

    This class only handles:
      - opening /dev/lora0
      - entering processor mode
      - applying EASEL config commands
      - entering operation mode via start
      - sending one ASCII line
      - receiving one ASCII line

    Higher-layer packet/token/BST-ID logic should be implemented elsewhere.
    """

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
        self.log(f"[RADIO] opening {self.port} baud={self.baudrate}")

        self.ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
        )

        # Avoid unintended modem-control behavior where possible.
        try:
            self.ser.setDTR(False)
            self.ser.setRTS(False)
        except Exception:
            pass

        # Match the timing style of the earlier working code.
        time.sleep(2.0)

        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

        self.read_available_lines(prefix="[BOOT]", duration_sec=1.0)

        self.log("[RADIO] opened")

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    # ------------------------------------------------------------
    # Reading helper
    # ------------------------------------------------------------

    def read_available_lines(self, prefix: str = "[RX]", duration_sec: float = 0.8) -> list[str]:
        """
        Read available text lines for a limited duration.
        Used mainly during configuration to capture OK/NG responses.
        """
        if self.ser is None:
            return []

        lines: list[str] = []
        t_end = time.time() + duration_sec

        while time.time() < t_end:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    lines.append(line)
                    self.log(f"{prefix} {line}")

            except Exception as e:
                self.log(f"{prefix}-ERR {e}")
                break

        return lines

    def read_line(self) -> Optional[str]:
        """
        Read one line in operation mode.

        Returns only application payload lines.
        Filters out modem responses such as OK/NG and mode prompts.
        """
        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        raw = self.ser.readline()
        if not raw:
            return None

        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            return None

        upper = line.upper()

        # Modem/config responses are not application payloads.
        if upper == "OK" or upper.startswith("NG"):
            self.log(f"[MODEM] {line}")
            return None

        if "SELECT MODE" in upper:
            self.log(f"[MODEM] {line}")
            return None

        self.log(f"[RX-LINE] {line}")
        return line

    # ------------------------------------------------------------
    # Command mode
    # ------------------------------------------------------------

    def send_cmd(self, cmd: str, wait_sec: float = 0.3) -> list[str]:
        """
        Send one processor-mode configuration command.
        """
        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        msg = cmd + "\r\n"
        self.log(f"[INIT-TX] {repr(msg)}")

        self.ser.write(msg.encode("utf-8", errors="ignore"))
        self.ser.flush()

        time.sleep(wait_sec)

        lines = self.read_available_lines(prefix="[INIT-RX]", duration_sec=0.4)
        return lines

    def configure_from_config(self) -> None:
        """
        Configure ES920LR using config.EASEL_CONFIG_COMMANDS.
        """
        self.log("[RADIO] configure ES920LR")

        # Select processor mode. If already in processor/config mode,
        # this typically returns OK or is harmless in this workflow.
        self.send_cmd("2")

        for cmd, value in config.EASEL_CONFIG_COMMANDS:
            self.send_cmd(f"{cmd} {value}")

        self.send_cmd("start", wait_sec=0.5)

        self.read_available_lines(prefix="[AFTER-START]", duration_sec=1.0)

        self.log("[RADIO] ready")

    # ------------------------------------------------------------
    # Operation mode send
    # ------------------------------------------------------------

    def write_line(self, line: str) -> None:
        if self.ser is None:
            raise RuntimeError("Serial port is not open")

        msg = line + "\r\n"
        self.ser.write(msg.encode("utf-8", errors="ignore"))
        self.ser.flush()

        self.log(f"[TX] {line}")

    def send_payload(self, payload: str, max_len: int = config.MAX_TX_LINE_LEN) -> bool:
        if payload is None:
            self.log("[TX-DROP] payload is None")
            return False

        if len(payload) > max_len:
            self.log(f"[TX-DROP] too long len={len(payload)} max={max_len}")
            return False

        self.write_line(payload)
        return True
