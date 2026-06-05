# -*- coding: utf-8 -*-
import config
from easel_radio import EaselRadio


def main():
    radio = EaselRadio(debug=True)

    radio.open()
    radio.configure_from_config()

    print("[RX-TEST] waiting...", flush=True)

    try:
        while True:
            line = radio.read_line()
            if line:
                print(f"[APP-RX] {line}", flush=True)

    finally:
        radio.close()


if __name__ == "__main__":
    main()
