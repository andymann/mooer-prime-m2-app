"""Human labels for module types, models and parameter words, plus value
formatting and slider ranges. Port of ModuleCatalog.swift; see that file
and docs/RESEARCH-NOTES.md for where each value comes from.

The official app shows the model as wire value + 1 (wire 1 -> "02 Flex Boost").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from .models import PrimeModule


class ParamKind(Enum):
    KNOB = auto()            # 0...100 knob
    MILLISECONDS = auto()    # high bit (0x8000) = tempo-synced, low bits = sync index
    HERTZ = auto()
    OFF_OR_VALUE = auto()    # Cab / EQ cuts: raw Hz, 0 = OFF
    EQ_GAIN = auto()         # dB = (raw - 32) / 2
    EQ_Q = auto()            # (raw + 2) / 10
    RATE = auto()            # 0...100; 101 + n = tempo-synced to sync note n
    PITCH = auto()           # semitones = raw / 10 - 12
    SIM_GAIN = auto()        # Guitar Sim Low/Mid/High: dB = (raw - 120) / 10
    MID_SHIFT = auto()       # Guitar Sim Mid Shift: Hz = raw + 200
    UNKNOWN = auto()


@dataclass(frozen=True)
class Param:
    label: str
    kind: ParamKind = ParamKind.KNOB
    # Raw slider range when it isn't implied by `kind` (see `slider_range`).
    range: Optional[Tuple[int, int]] = None

    @property
    def syncable(self) -> bool:
        """Has a tempo-sync button (Delay Time, Mod Rate)."""
        return self.kind == ParamKind.RATE or (self.kind == ParamKind.MILLISECONDS and self.label == "Time")


TYPE_NAMES: Dict[int, str] = {
    0x01: "Drive",
    0x02: "Pre-Amp",
    0x03: "Cab",
    0x04: "Dyna",
    0x05: "Filter",
    0x06: "Mod",
    0x07: "Delay",
    0x08: "Reverb",
    0x09: "EQ",
    0x0A: "Guitar Sim",
    0x0B: "Entire Amp",
}

# Keyed by type, then by wire model value (app number - 1).
MODEL_NAMES: Dict[int, Dict[int, str]] = {
    0x01: {0: "Pure Boost", 1: "Flex Boost", 2: "Tube DR", 3: "808", 4: "Gold Clon", 5: "D-Drive", 6: "Jimmy OD", 7: "Full DR", 8: "Beebee Pre", 9: "Beebee+", 10: "Black Rat", 11: "Grey Faze", 12: "Muffy", 13: "Full DS", 14: "Shred", 15: "Riet", 16: "MTL Zone", 17: "MTL Master", 18: "Obsessive Dist", 19: "Rumble Drive", 20: "Solo", 21: "Blues Mood", 22: "Blues Crab", 23: "Blade", 24: "TS9 Boost", 25: "ROD881", 26: "Tethys 21", 27: "TS Drive", 28: "Blackglass X8"},
    0x02: {0: "65 US DLX", 1: "65 US TW", 2: "59 US Bass", 3: "US Sonic", 4: "US Blues CL", 5: "US Blues OD", 6: "E650 CL", 7: "Powerbell CL", 8: "Blacknight CL", 9: "Mark III CL", 10: "Mark V CL", 11: "Tri Rec CL", 12: "Rockvrb CL", 13: "Dr Zee 18 JR", 14: "Dr Zee Reck", 15: "Jet 100H CL", 16: "Jazz 120", 17: "UK 30 CL", 18: "UK 30 OD", 19: "HWT 103", 20: "PV5050 CL", 21: "Regal Tone CL", 22: "Regal Tone OD1", 23: "Carol CL", 24: "Cardeff", 25: "EV 5050 CL", 26: "HT Club CL", 27: "Hugen CL", 28: "Koche OD", 29: "J800", 30: "J900", 31: "PLX 100", 32: "E650 DS", 33: "Powerbell DS", 34: "Blacknight DS", 35: "Mark III DS", 36: "Mark V DS", 37: "Tri Rec DS", 38: "Rockvrb DS", 39: "Citrus 30", 40: "Citrus 50", 41: "Slow 100 CR", 42: "Slow 100 DS", 43: "Jet 100H OD", 44: "PV 5050 DS", 45: "Regal Tone OD2", 46: "Carol OD", 47: "EV 5050 DS", 48: "HT Club DS", 49: "Hugen OD", 50: "Hugen DS", 51: "Koche DS", 52: "AMPOG 2OD", 53: "AMPOG B18 CL", 54: "AMPOG SVT 4", 55: "AMPOG SVT VALVE", 56: "MVRKBASS 500", 57: "MVRKBASS 501", 58: "AKUILA 750 CL", 59: "AKUILA 750 DS", 60: "AKUILA 751", 61: "Basser Crunch"},
    0x03: {0: "Regal Tone 110", 1: "US DLX 112", 2: "Sonic 112", 3: "Blues 112", 4: "Mark 112", 5: "Dr Zee 112", 6: "Cardeff 112", 7: "US TW 212", 8: "Citrus 212", 9: "Dr Zee 212", 10: "Jazz 212", 11: "UK 212", 12: "Tow Stones 212", 13: "US Bass 410", 14: "1960 412", 15: "Eagle P412", 16: "Eagle S412", 17: "Rec 412", 18: "Citrus 412", 19: "Slow 412", 20: "HWT 412", 21: "PV 5050 412", 22: "EV 5050 412", 23: "HT 412", 24: "Diesel 412", 25: "CT-SupBMK112", 26: "CT-FendS212", 27: "CT-FendTW212", 28: "CT-Fend67212", 29: "CT-BritJV212", 30: "CT-Brit412", 31: "CT-BritJ412", 32: "CT-Bogie212", 33: "CT-BogieLS212", 34: "CT-BogOS412", 35: "CT-Vocs212", 36: "CT-Barb212", 37: "CT-Fram212", 38: "CT-Kox212", 39: "CT-Mgan212", 40: "CT-Edd412", 41: "CT-Fried412", 42: "CT-Gas412", 43: "CT-Hess212", 44: "CT-Hess412", 45: "CT-HW412", 46: "CT-HK412", 47: "CT-OR412", 48: "CT-Pvyln212", 49: "CT-Pvy50412", 50: "CT-Revo412", 51: "CT-River412", 52: "CT-Sold412", 53: "CT-VTH412", 54: "CT-Win412", 55: "SV810 U47", 56: "SV810 121", 57: "SV810 7B", 58: "HK U47", 59: "HK 7B", 60: "HK 121", 61: "HK 421", 62: "Akuila U47", 63: "Akuila 7B", 64: "Akuila 121"},
    0x04: {0: "Red Comp", 1: "Yellow Comp", 2: "Noise Killer", 3: "Intel Reducer"},
    0x05: {0: "Touch Wah", 1: "Auto Wah", 2: "Q-Filter"},
    0x06: {0: "Phaser", 1: "Step Phaser", 2: "Flanger", 3: "Jet Flanger", 4: "Tremolo", 5: "Stutter", 6: "Vibrato", 7: "Rotary", 8: "Ring", 9: "Pitch Shift", 10: "Ana Chorus", 11: "Tri Chorus", 12: "Lofi", 13: "Slow Gear"},
    0x07: {0: "Digital", 1: "Analog", 2: "Real Echo", 3: "Tape", 4: "Mod", 5: "Reverse", 6: "PingPong", 7: "Crystal", 8: "Rainbow"},
    0x08: {0: "Room", 1: "Hall", 2: "Plate", 3: "Spring", 4: "Mod", 5: "Shimmer"},
    0x09: {0: "5 Bands EQ", 1: "Custom EQ"},
    0x0A: {0: "Ac Electric GBS 45 a", 1: "Ac Electric GBS 45 b", 2: "Ac Electric GBS 45 c", 3: "Ac Electric GBS SJ200 a", 4: "Ac Electric GBS SJ200 b", 5: "Ac Electric GBS SJ200 c", 6: "Ac Electric GLD D55 a", 7: "Ac Electric GLD D55 b", 8: "Ac Electric GLD D55 c", 9: "Ac Electric MTN D45 a", 10: "Ac Electric MTN D45 b", 11: "Ac Electric MTN D45 c", 12: "Ac Electric TLR 614a", 13: "Ac Electric TLR 614b", 14: "Ac Electric TLR 614c", 15: "Ac Piezo GBS 45 a", 16: "Ac Piezo GBS 45 b", 17: "Ac Piezo GBS 45 c", 18: "Ac Piezo GBS SJ200 a", 19: "Ac Piezo GBS SJ200 b", 20: "Ac Piezo GBS SJ200 c", 21: "Ac Piezo GLD D55 a", 22: "Ac Piezo GLD D55 b", 23: "Ac Piezo GLD D55 c", 24: "Ac Piezo MTN D45 a", 25: "Ac Piezo MTN D45 b", 26: "Ac Piezo MTN D45 c", 27: "Ac Piezo TLR 614a", 28: "Ac Piezo TLR 614b", 29: "Ac Piezo TLR 614c", 30: "Ac Silent GBS 45 a", 31: "Ac Silent GBS 45 b", 32: "Ac Silent GBS 45 c", 33: "Ac Silent GBS SJ200 a", 34: "Ac Silent GBS SJ200 b", 35: "Ac Silent GBS SJ200 c", 36: "Ac Silent GLD D55 a", 37: "Ac Silent GLD D55 b", 38: "Ac Silent GLD D55 c", 39: "Ac Silent MTN D45 a", 40: "Ac Silent MTN D45 b", 41: "Ac Silent MTN D45 c", 42: "Ac Silent TLR 614a", 43: "Ac Silent TLR 614b", 44: "Ac Silent TLR 614c", 45: "Cl Piezo Corba GK a", 46: "Cl Piezo Corba GK b", 47: "Cl Piezo Corba GK c", 48: "Cl Piezo YMH 42S a", 49: "Cl Piezo YMH 42S b", 50: "Cl Piezo YMH 42S c", 51: "Cl Silent Corba GK a", 52: "Cl Silent Corba GK b", 53: "Cl Silent Corba GK c", 54: "Cl Silent YMH 42S a", 55: "Cl Silent YMH 42S b", 56: "Cl Silent YMH 42S c"},
    0x0B: {0: "55 US TD", 1: "59 US Bass", 2: "UK30 CL", 3: "UK30 CR", 4: "UK30 DS", 5: "ODS 100 CL", 6: "ODS 100 CR", 7: "ODS 100 DS", 8: "Dividers CL", 9: "Dividers DS", 10: "CAA OD100 CH1", 11: "CAA OD100 CH2", 12: "CAA OD100 CH3", 13: "Rock Vrb CL", 14: "Rock Vrb DS", 15: "J800 CL", 16: "J800 CR", 17: "J800 DS", 18: "PLX 100 CL", 19: "PLX 100 DS", 20: "EV 5050 CH1", 21: "EV 5050 CH2", 22: "EV 5050 CH3", 23: "Cali BLD CL", 24: "Cali BLD CR", 25: "Cali BLD DS"},
}

_p = Param
K = ParamKind

# Models whose parameters differ from their type's default list.
MODEL_PARAMS: Dict[int, Dict[int, List[Param]]] = {
    0x04: {0: [_p("Sensitivity"), _p("Level")],
           2: [_p("Threshold"), _p("Level")]},
    # Touch Wah word order assumed from the app's display order.
    0x05: {0: [_p("Peak"), _p("Sensitivity"), _p("Level")],
           2: [_p("Rate"), _p("Q"), _p("Mix")]},
    0x06: {0: [_p("Rate", K.RATE), _p("Level"), _p("Depth")],
           1: [_p("Rate", K.RATE), _p("Level"), _p("Depth")],
           2: [_p("Rate", K.RATE), _p("Mix"), _p("Feedback")],
           3: [_p("Rate", K.RATE), _p("Mix"), _p("Feedback")],
           4: [_p("Rate", K.RATE), _p("Mix"), _p("Tone")],
           5: [_p("Rate", K.RATE), _p("Mix"), _p("Tone")],
           6: [_p("Rate", K.RATE), _p("Mix"), _p("Tone")],
           7: [_p("Rate", K.RATE), _p("Mix"), _p("Tone")],
           8: [_p("Rate", K.RATE), _p("Mix"), _p("Tone")],
           9: [_p("Mix"), _p("Tone"), _p("Pitch", K.PITCH)],
           12: [_p("Sample"), _p("Mix"), _p("Bit")],
           13: [_p("Rise"), _p("Level")]},
    # Delay words 4-5 are hidden in the official app except on these models.
    0x07: {7: [_p("Level"), _p("Feedback"), _p("Time", K.MILLISECONDS, (40, 2500)), _p("Depth"), _p("Rate")],
           8: [_p("Level"), _p("Feedback"), _p("Time", K.MILLISECONDS, (40, 2500)), _p("Depth"), _p("Rate")]},
    # Words after Level are not shown in the official app.
    0x09: {0: [_p(label, K.EQ_GAIN) for label in ("100Hz", "250Hz", "630Hz", "1.6kHz", "4kHz")] + [_p("Level")]},
}


def _eq_params() -> List[Param]:
    # Band frequency ranges in Hz, from the official app.
    bands = [("Low", (40, 600)), ("Low Mid", (200, 2000)), ("High Mid", (600, 6000)), ("High", (1500, 20000))]
    eq: List[Param] = []
    for band, frequencies in bands:
        eq += [_p(f"{band} Gain", K.EQ_GAIN), _p(f"{band} Freq", K.HERTZ, frequencies), _p(f"{band} Q", K.EQ_Q)]
    # Cuts like the Cab's: 0 = OFF, Low Cut up to 600 Hz, High Cut up to 2000 Hz.
    eq += [_p("Low Cut", K.OFF_OR_VALUE, (0, 600)), _p("High Cut", K.OFF_OR_VALUE, (0, 2000)), _p("Level")]
    return eq


# Parameters in word order (after enabled + model).
PARAMS: Dict[int, List[Param]] = {
    0x01: [_p("Gain"), _p("Tone"), _p("Vol")],
    0x02: [_p("Gain"), _p("Bass"), _p("Mid"), _p("Treble"), _p("Presence"), _p("Master")],
    0x03: [_p("Gain"), _p("Low Cut", K.OFF_OR_VALUE, (0, 600)), _p("High Cut", K.OFF_OR_VALUE, (0, 2000))],
    0x04: [_p("Attack"), _p("Ratio"), _p("Threshold"), _p("Level")],
    0x05: [_p("Rate"), _p("Peak"), _p("Range"), _p("Level")],
    0x06: [_p("Rate", K.RATE), _p("Mix"), _p("Tone"), _p("Depth")],
    0x07: [_p("Level"), _p("Feedback"), _p("Time", K.MILLISECONDS, (40, 2500))],
    0x08: [_p("Pre Delay", K.MILLISECONDS, (0, 200)), _p("Level"), _p("Decay"), _p("Tone")],
    0x09: _eq_params(),
    # 9 more words follow that the official app doesn't show (DSP coefficients).
    0x0A: [_p("Gain"), _p("Blend"), _p("Low", K.SIM_GAIN), _p("Mid", K.SIM_GAIN),
           _p("Mid Shift", K.MID_SHIFT), _p("High", K.SIM_GAIN)],
    # Same layout as a Pre-Amp.
    0x0B: [_p("Gain"), _p("Bass"), _p("Mid"), _p("Treble"), _p("Presence"), _p("Master")],
}

# Categories in the official app's picker order.
CATEGORY_ORDER: List[int] = [0x0A, 0x01, 0x02, 0x03, 0x0B, 0x04, 0x05, 0x09, 0x06, 0x07, 0x08]

# Slots the editor offers (9, as in the official app).
MAX_MODULES = 9

# Tempo-sync notes in the official app's slider order with their stored
# index. Delay (0x8000 | index) and Mod Rate (101 + index) share it.
SYNC_NOTE_ORDER: List[Tuple[int, str]] = [
    (2, "1/32"), (4, "1/32D"), (0, "1/32T"), (5, "1/16"), (7, "1/16D"), (1, "1/16T"),
    (8, "1/8"), (9, "1/8D"), (3, "1/8T"), (10, "1/4"), (12, "1/4D"), (6, "1/4T"),
    (13, "1/2"), (14, "1/2D"), (11, "1/2T"), (15, "1/1"),
]
SYNC_NOTES: Dict[int, str] = dict(SYNC_NOTE_ORDER)


def models_of(kind: int) -> List[int]:
    """Wire model values with names, in order."""
    return sorted(MODEL_NAMES.get(kind, {}))


def is_unnamed(module: PrimeModule) -> bool:
    """True when the module's type or model has no name in the tables yet."""
    if module.is_empty:
        return False
    if module.type not in TYPE_NAMES or module.model is None:
        return True
    return module.model not in MODEL_NAMES.get(module.type, {})


def sync_label(index: int) -> str:
    return "♩ " + SYNC_NOTES.get(index, f"sync #{index}")


def sync_index(kind: ParamKind, value: int) -> Optional[int]:
    """The sync-note index when a Delay Time or Mod Rate value is tempo-synced."""
    if kind == ParamKind.MILLISECONDS:
        return value & 0x7FFF if value & 0x8000 else None
    if kind == ParamKind.RATE:
        return value - 101 if value > 100 else None
    return None


def synced_value(kind: ParamKind, index: int) -> int:
    return 101 + index if kind == ParamKind.RATE else 0x8000 | index


def type_name(kind: int) -> str:
    return TYPE_NAMES.get(kind, f"Type 0x{kind:02x}")


def model_label(kind: int, model: int) -> str:
    """"02 Flex Boost", or just "07" when the name isn't known yet."""
    number = f"{model + 1:02d}"
    name = MODEL_NAMES.get(kind, {}).get(model)
    return f"{number} {name}" if name else number


def param(kind: int, model: Optional[int], param_index: int) -> Param:
    """`param_index` counts from 0 at the first word after enabled + model."""
    plist = MODEL_PARAMS.get(kind, {}).get(model) if model is not None else None
    if plist is None:
        plist = PARAMS.get(kind)
    if plist is not None and param_index < len(plist):
        return plist[param_index]
    return Param(f"P{param_index + 1}", ParamKind.UNKNOWN)


def format_value(value: int, kind: ParamKind) -> str:
    if kind in (ParamKind.KNOB, ParamKind.UNKNOWN):
        return str(value)
    if kind == ParamKind.MILLISECONDS:
        return sync_label(value & 0x7FFF) if value & 0x8000 else f"{value} ms"
    if kind == ParamKind.HERTZ:
        return f"{value} Hz"
    if kind == ParamKind.OFF_OR_VALUE:
        return "OFF" if value == 0 else f"{value} Hz"
    if kind == ParamKind.EQ_GAIN:
        return f"{(value - 32) / 2:+.1f} dB"
    if kind == ParamKind.EQ_Q:
        return f"{(value + 2) / 10:.1f}"
    if kind == ParamKind.RATE:
        return sync_label(value - 101) if value > 100 else str(value)
    if kind == ParamKind.PITCH:
        return f"{value / 10 - 12:.1f}"
    if kind == ParamKind.SIM_GAIN:
        return f"{(value - 120) / 10:+.1f} dB"
    if kind == ParamKind.MID_SHIFT:
        return f"{value + 200} Hz"
    return str(value)


def slider_range(p: Param, value: int) -> Optional[Tuple[int, int]]:
    """Raw slider range, or None when the word gets a number field (also when
    the stored value lies outside the known range)."""
    if p.kind in (ParamKind.KNOB, ParamKind.RATE, ParamKind.UNKNOWN):
        rng = p.range or (0, 100)
    elif p.kind == ParamKind.SIM_GAIN:
        rng = (0, 240)
    elif p.kind == ParamKind.MID_SHIFT:
        rng = (0, 1800)
    elif p.kind == ParamKind.EQ_GAIN:
        rng = (0, 64)
    elif p.kind == ParamKind.EQ_Q:
        rng = (0, 98)
    elif p.kind == ParamKind.PITCH:
        rng = (0, 240)
    else:
        # Delay Time 40...2500 ms; tempo-synced values (0x8000 | note) fall
        # outside the range and keep a number field.
        rng = p.range
    if rng is None or not (rng[0] <= value <= rng[1]):
        return None
    return rng
