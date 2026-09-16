# -*- coding: utf-8 -*-

import json
import time

import pynmea2
import redis
import serial

import config


def main():

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )

    gps = serial.Serial(
        config.GPS_SERIAL_PORT,
        config.GPS_BAUDRATE,
        timeout=1,
    )

    print(
        f"[GPS-REDIS] started "
        f"port={config.GPS_SERIAL_PORT} "
        f"baud={config.GPS_BAUDRATE}",
        flush=True,
    )

    try:

        while True:

            line = gps.readline().decode(
                "ascii",
                errors="ignore",
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

            lat = float(msg.latitude)
            lon = float(msg.longitude)

            state = {
                "lat": lat,
                "lon": lon,
                "source": "gps",
                "updated_at": time.time(),
            }

            r.set(
                config.REDIS_GPS_STATE_KEY,
                json.dumps(state),
            )

            print(
                f"[GPS] lat={lat:.8f} "
                f"lon={lon:.8f}",
                flush=True,
            )

    except KeyboardInterrupt:

        print(
            "[GPS-REDIS] stopped",
            flush=True,
        )

    finally:

        gps.close()


if __name__ == "__main__":
    main()
