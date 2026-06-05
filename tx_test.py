# -*- coding: utf-8 -*-
import time

import config
from easel_radio import EaselRadio


def main():
    radio = EaselRadio(debug=True)

    radio.open()
    radio.configure_from_config()

    print("[TX-TEST] sending...", flush=True)

    count = 0

    try:
        while True:
            msg = f"T,{config.NODE_ID},{count}"
            radio.send_payload(msg, max_len=config.MAX_TX_LINE_LEN)
            count += 1
            time.sleep(3)

    finally:
        radio.close()


if __name__ == "__main__":
    main()
