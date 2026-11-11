
# -*- coding: utf-8 -*-
"""Select LoRa radio backends without coupling routing to hardware."""
import config


def create_radio(debug=True):
    backend = getattr(config, "RADIO_TYPE", "easel").lower()
    if backend == "easel":
        from easel_radio import EaselRadio
        return EaselRadio(debug=debug)
    if backend == "flint":
        from flint_radio import FlintRadio
        return FlintRadio(debug=debug)
    raise ValueError(f"unknown RADIO_TYPE: {backend!r}; expected easel or flint")
