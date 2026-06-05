# -*- coding: utf-8 -*-
import math
from typing import Any, Dict, Optional, Tuple

from bst_id.encoder import BSTIDEncoder


def encode_xy_bst_id(
    lat: float,
    lon: float,
    zx: int,
    zy: int,
) -> Dict[str, Any]:
    """
    Encode lat/lon into official BST-ID using bst-id-main.

    BST-ID axes:
      x = longitude
      y = latitude
    """

    bst_id, bit_len = BSTIDEncoder.encode(
        x=float(lon),
        y=float(lat),
        f=None,
        t_unix=None,
        zoom_x=int(zx),
        zoom_y=int(zy),
        zoom_f=0,
        zoom_t=0,
    )

    byte_len = (bit_len + 7) // 8
    bst_hex = int(bst_id).to_bytes(byte_len, "big").hex().upper()

    return {
        "bst_id": int(bst_id),
        "bit_len": int(bit_len),
        "byte_len": int(byte_len),
        "bst_hex": bst_hex,
        "zx": int(zx),
        "zy": int(zy),
    }


def decode_bst_hex_to_int(bst_hex: str) -> int:
    return int(bst_hex, 16)


def bst_hex_to_bin(bst_hex: str, bit_len: int) -> str:
    v = int(bst_hex, 16)
    return format(v, f"0{bit_len}b")


# ------------------------------------------------------------
# Display/helper tile functions
# These are not the official BST-ID encoder.
# They are used only to estimate tile center for map display.
# ------------------------------------------------------------

def lon_to_xtile(lon: float, z: int) -> int:
    n = 2 ** z
    return int(math.floor(((lon + 180.0) / 360.0) * n))


def lat_to_ytile(lat: float, z: int) -> int:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** z
    lat_rad = math.radians(lat)

    return int(math.floor(
        (
            0.5
            - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad))
            / (2.0 * math.pi)
        ) * n
    ))


def xtile_to_lon(x: int, z: int) -> float:
    n = 2 ** z
    return x / n * 360.0 - 180.0


def ytile_to_lat(y: int, z: int) -> float:
    n = 2 ** z
    merc_n = math.pi * (1.0 - 2.0 * y / n)
    return math.degrees(math.atan(math.sinh(merc_n)))


def estimate_tile_center_from_latlon(lat: float, lon: float, z: int) -> Dict[str, Any]:
    """
    Estimate map display tile center from lat/lon and zoom.
    Used for Redis/Web display only.
    """
    x = lon_to_xtile(lon, z)
    y = lat_to_ytile(lat, z)

    west = xtile_to_lon(x, z)
    east = xtile_to_lon(x + 1, z)
    north = ytile_to_lat(y, z)
    south = ytile_to_lat(y + 1, z)

    return {
        "z": z,
        "xtile": x,
        "ytile": y,
        "lat": (north + south) / 2.0,
        "lon": (west + east) / 2.0,
        "bounds": {
            "north": north,
            "south": south,
            "west": west,
            "east": east,
        }
    }
