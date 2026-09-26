"""Where the parts of box v1 sit on the board (standard library only).

Shared by pcb.py (the KiCad board) and mechanical.py (the enclosure openings). Coordinates are
millimetres from the top-left corner of the board, x to the right, y down; rotations are degrees
counter-clockwise. The iPhone port is on the left edge (the front of the box); Ethernet, the PC port
and the power input are on the right edge (the rear).
"""

W, H = 96.0, 66.0           # board size
CORNER_R = 2.0              # rounded board corners
MH = [(3.5, 3.5), (3.5, H - 3.5), (W - 3.5, H - 3.5), (W - 3.5, 3.5)]   # M2.5 mounting holes
FIDUCIALS = [(15.0, H - 1.8), (66.0, H - 1.8), (40.0, 1.8)]
# Routing corridors kept free of small parts: the DP lanes between the port's ESD and the LT7911D,
# and the CSI-2 lanes between the LT7911D and the module's CSI pads (left edge, pads 1-12).
RESERVED = [(14.4, 30.0, 22.2, 38.4), (31.6, 6.4, 38.05, 30.5)]

MOD = (54.0, 21.5)           # Core1106 centre
LTC = (26.5, 34.0)           # LT7911D centre
MCU = (19.5, 16.5)           # CH32V305 centre (rotated 180°: USB HS pins face the iPhone port)

PLACE = {
    # front (left) edge: iPhone port and its ESD, status LEDs behind light pipes
    "J201": (4.8, 35.0, 270),
    "U202": (12.9, 32.9, 0), "U203": (12.9, 37.1, 0), "U204": (16.1, 35.0, 0), "U205": (14.4, 27.0, 0),
    "D501": (2.2, 11.5, 0), "D502": (2.2, 14.0, 0), "D503": (2.2, 16.5, 0),
    "R501": (5.4, 11.5, 0), "R502": (5.4, 14.0, 0), "R503": (5.4, 16.5, 0),
    # iPhone VBUS switch, below the iPhone port
    "Q102": (6.8, 47.6, 0), "Q101": (6.8, 54.2, 0), "R126": (13.4, 47.6, 90), "U106": (14.0, 53.8, 0),
    "Q103": (13.8, 58.8, 0), "JP101": (9.2, 61.4, 0), "D201": (5.2, 42.2, 0),
    # LT7911D (provisional footprint) and its crystal
    "U201": (LTC[0], LTC[1], 0), "X201": (34.0, 41.0, 0),
    # CH32V305 and its crystal
    "U301": (MCU[0], MCU[1], 180), "X301": (29.8, 18.6, 90),
    # debug headers
    "J501": (9.5, 2.6, 90), "J502": (29.5, 2.6, 90), "J503": (53.5, 2.2, 90),
    # Core1106 in the middle, Ethernet in the rear-top corner
    "U401": (MOD[0], MOD[1], 0),
    "J402": (W - 17.76, 21.44, 90),
    # rear edge: PC USB-C, CC-line ESD, power USB-C and its input parts
    "J401": (W - 4.15, 34.2, 90), "U402": (83.3, 34.2, 0), "U107": (84.0, 41.6, 90),
    "J101": (W - 4.15, 49.0, 90), "F101": (83.8, 49.0, 90), "D101": (79.4, 49.2, 90),
    "C101": (82.5, 59.6, 0), "U101": (77.2, 37.6, 0), "D104": (78.6, 43.6, 0),
    "SW501": (72.0, 61.2, 0), "SW502": (72.0, 55.2, 0),
    # 3V3 (U104) and 1V2 (U105) bucks, SW pin towards the inductor
    "U104": (23.5, 48.0, 180), "L103": (29.3, 48.0, 0),
    "U105": (23.5, 58.5, 180), "L104": (29.3, 58.5, 0),
    # 5V2_PHONE (U102) and 5V_SYS (U103) bucks: inductor above, its SW pad next to the SW pin
    "U102": (43.0, 60.9, 0), "L101": (45.5, 50.3, 90),
    "U103": (59.5, 60.9, 0), "L102": (62.0, 50.3, 90),
}

# Output capacitors of the bucks, in a column beside the inductor's output pad: (caps, pad, side)
OUTCAPS = {
    "L101": (["C108", "C109", "C110"], "2", -1), "L102": (["C116", "C117", "C118"], "2", -1),
    "L103": (["C121", "C122"], "2", 1), "L104": (["C126", "C127"], "2", 1),
}
# Input capacitors of the 3 A bucks: beside VIN/GND (pins 2/1), (caps, side)
INCAPS = {"U102": ["C103", "C104"], "U103": ["C111", "C112"]}

# Small parts that should stay near a given point rather than their net centroid
HINTS = {
    "D201": None, "C219": (10.8, 42.2), "C220": (13.6, 42.2), "R210": (16.2, 42.4),
    "R405": (71.0, 3.2), "R406": (71.0, 4.6), "R407": (68.6, 3.2), "R408": (68.6, 4.6),
    "C401": (71.6, 12.0), "C402": (71.6, 14.4), "TP401": (70.6, 3.0),
    # the LT7911D's I2C pull-ups, interrupt pull-down and reset parts sit by the LT7911D, keeping the
    # corridor between the module and the RJ45 free for the module's right-edge signals
    "R201": (31.4, 38.6), "R202": (31.4, 39.8), "R205": (33.8, 36.6),
    "C406": (73.0, 18.0), "C407": (73.0, 16.2), "C408": (72.4, 25.0),
    "R411": (36.0, 35.0),
    # decoupling of LT7911D pins that have no pad yet (placeholders): beside U201 until the pin table is in
    "C209": (31.2, 31.4), "C210": (31.2, 32.6), "C211": (31.2, 33.8),
    "TP507": (2.4, 25.0), "TP508": (86.0, 27.2), "TP501": (86.2, 56.0),
    "R409": (68.0, 40.0), "R410": (68.0, 41.2), "C409": (68.0, 42.4),
    "JP102": (84.2, 45.4),
}
