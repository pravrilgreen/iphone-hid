"""iPhone models the simulator can play: logical size in points, pixel scale, connector."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhoneModel:
    key: str
    name: str
    width_pt: int  # portrait
    height_pt: int
    scale: int  # device pixels per point
    port: str  # "usb-c" or "lightning"
    home_button: bool = False

    @property
    def pixels(self) -> tuple[int, int]:
        return self.width_pt * self.scale, self.height_pt * self.scale

    @property
    def aspect(self) -> float:
        return self.width_pt / self.height_pt


MODELS = {
    m.key: m
    for m in (
        PhoneModel("iphone-17", "iPhone 17", 402, 874, 3, "usb-c"),
        PhoneModel("iphone-16", "iPhone 16", 393, 852, 3, "usb-c"),
        PhoneModel("iphone-15", "iPhone 15", 393, 852, 3, "usb-c"),
        PhoneModel("iphone-15-pro-max", "iPhone 15 Pro Max", 430, 932, 3, "usb-c"),
        PhoneModel("iphone-13", "iPhone 13", 390, 844, 3, "lightning"),
        PhoneModel("iphone-11", "iPhone 11", 414, 896, 2, "lightning"),
        PhoneModel("iphone-se-3", "iPhone SE (3rd generation)", 375, 667, 2, "lightning", home_button=True),
    )
}


def get_model(key: str) -> PhoneModel:
    try:
        return MODELS[key]
    except KeyError:
        raise ValueError(f"unknown phone model {key!r}; known: {', '.join(MODELS)}") from None
