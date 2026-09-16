# -*- coding: utf-8 -*-

import json
import time

import pynmea2
import redis
import serial

import config


def write_position(r, lat, lon, source):
    state = {
        "lat": float(lat),
        "lon": float(lon),
        "source": source,
        "updated_at": time.time(),
    }

    r.set(
        config.REDIS_GPS_STATE_KEY,
        json.dumps(state),
    )


def main():

    # ============================================================
    # Redis
    # ============================================================

    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )


    # ============================================================
    # Dummy position
    # ============================================================

    if config.USE_DUMMY_GPS:

        print(
            f"[GPS-REDIS] dummy mode "
            f"lat={config.DUMMY_LAT} "
            f"lon={config.DUMMY_LON}",
            flush=True,
        )

        while True:

            write_position(
                r,
                config.DUMMY_LAT,
                config.DUMMY_LON,
                "dummy",
            )

            time.sleep(1)


    # ============================================================
    # Real GPS
    # ============================================================

    print(
        f"[GPS-REDIS] real GPS mode "
        f"port={config.GPS_SERIAL_PORT} "
        f"baud={config.GPS_BAUDRATE}",
        flush=True,
    )

    gps = serial.Serial(
        config.GPS_SERIAL_PORT,
        config.GPS_BAUDRATE,
        timeout=1,
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

            write_position(
                r,
                lat,
                lon,
                "gps",
            )

            print(
                f"[GPS] "
                f"lat={lat:.8f} "
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
