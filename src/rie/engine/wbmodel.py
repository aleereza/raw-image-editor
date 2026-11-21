"""RawTherapee's white balance model: Temperature and Green to channel multipliers, and back.

A recipe's custom white balance is RawTherapee's `[White Balance] Temperature` and `Green`. RawTherapee turns them
into raw channel multipliers in two steps (5.13, `rtengine/colortemp.cc` and `rawimagesource.cc`):

1. The illuminant's chromaticity: a blackbody (Planck's law) up to 4000 K, the CIE daylight locus above, integrated
   with the CIE colour matching functions of the profile's standard observer (2 degrees for profiles made by 5.10 or
   later, 10 degrees for older ones). Its XYZ, with Y = 1, goes to linear sRGB; Green divides the G channel, and the
   result is scaled so its largest channel is 1.
2. The camera's view of that illuminant: dcraw's camera matrix (`cam_rgb`, sRGB to camera, rows normalized so that
   sRGB white gives the camera's daylight multipliers `pre_mul`) maps it to camera RGB, and each channel's
   multiplier is `pre_mul / camera RGB`.

The inverse finds the temperature from the blue/red ratio by bisection with Green at 1, then Green from green/red,
as RawTherapee's `mul2temp` does. The camera's own (as-shot) multipliers map to the temperature and green that the
RawTherapee GUI shows for "Camera". docs/adr/0012-edit-agents-v1.md records the renders that confirmed this model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cache

import numpy as np

INITIAL_BLACKBODY = 4000.0  # K: blackbody below, daylight locus above
MIN_TEMP, MAX_TEMP = 1500.0, 60000.0
MIN_GREEN, MAX_GREEN = 0.02, 10.0  # RawTherapee allows up to 100; the recipe schema stops at 10
PLANCK_C1 = 3.7417715247e-16  # W m^2 (2 pi h c^2)
PLANCK_C2 = 1.438786e-2  # m K (h c / k)

# XYZ (Y = 1) to linear sRGB, D65, as RawTherapee's sRGBd65_xyz.
SRGB_D65_FROM_XYZ = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ]
)
# Linear sRGB to XYZ, D65, as dcraw's xyz_rgb (used to build the camera matrix).
XYZ_FROM_SRGB = np.array(
    [
        [0.412453, 0.357580, 0.180423],
        [0.212671, 0.715160, 0.072169],
        [0.019334, 0.119193, 0.950227],
    ]
)

OBSERVERS = {2: "CIE 1931 2 Degree Standard Observer", 10: "CIE 1964 10 Degree Standard Observer"}


@cache
def _tables(observer: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Wavelengths (m), colour matching functions and the daylight basis S0, S1, S2, sampled every 5 nm.

    colour-science holds the CIE tables; it takes about two seconds to import, so it is loaded on first use.
    """
    import colour

    cmfs = colour.MSDS_CMFS[OBSERVERS[observer]]
    nm = np.arange(360.0, 830.0 + 0.1, 5.0)
    cmf = np.array([cmfs[w] for w in nm])
    basis = colour.colorimetry.SDS_BASIS_FUNCTIONS_CIE_ILLUMINANT_D_SERIES
    # The 10 nm daylight basis, interpolated linearly to 5 nm: this matches RawTherapee's renders within 0.2 %,
    # where reading the 10 nm sample at or below each wavelength was 1-1.5 % off in blue (ADR 0012).
    daylight = np.array([[basis[name][w] for name in ("S0", "S1", "S2")] for w in nm])
    return nm * 1e-9, cmf, daylight


def _xy_from_spectrum(power: np.ndarray, cmf: np.ndarray) -> tuple[float, float]:
    xyz = power @ cmf
    total = float(xyz.sum())
    return float(xyz[0] / total), float(xyz[1] / total)


def daylight_xy(temp: float) -> tuple[float, float]:
    """The CIE daylight locus chromaticity at `temp` (4000-25000 K formulas, extended above as RawTherapee does)."""
    t = temp
    if t <= 7000:
        x = -4.6070e9 / t**3 + 2.9678e6 / t**2 + 0.09911e3 / t + 0.244063
    else:
        x = -2.0064e9 / t**3 + 1.9018e6 / t**2 + 0.24748e3 / t + 0.237040
        if t > 25000:
            x -= (t - 25000) / 25000 * 0.025
    return x, -3.0 * x * x + 2.87 * x - 0.275


def illuminant_xy(temp: float, observer: int = 2) -> tuple[float, float]:
    """Chromaticity of RawTherapee's illuminant at `temp` kelvin: blackbody up to 4000 K, daylight above."""
    wavelengths, cmf, daylight = _tables(observer)
    if temp <= INITIAL_BLACKBODY:
        power = (PLANCK_C1 / wavelengths**5) / np.expm1(PLANCK_C2 / (wavelengths * temp))
    else:
        xd, yd = daylight_xy(temp)
        d = 0.0241 + 0.2562 * xd - 0.734 * yd
        m1 = (-1.3515 - 1.7703 * xd + 5.9114 * yd) / d
        m2 = (0.03 - 31.4424 * xd + 30.0717 * yd) / d
        power = daylight @ np.array([1.0, m1, m2])
    return _xy_from_spectrum(power, cmf)


def temp_to_srgb(temp: float, green: float = 1.0, observer: int = 2) -> np.ndarray:
    """RawTherapee's `temp2mul`: the illuminant in linear sRGB with Green applied, largest channel 1."""
    temp = min(max(temp, MIN_TEMP), MAX_TEMP)
    green = min(max(green, MIN_GREEN), MAX_GREEN)
    x, y = illuminant_xy(temp, observer)
    xyz = np.array([x / y, 1.0, (1.0 - x - y) / y])
    rgb = SRGB_D65_FROM_XYZ @ xyz
    rgb[1] /= green
    return rgb / rgb.max()


def srgb_to_temp(rgb: np.ndarray, observer: int = 2) -> tuple[float, float]:
    """RawTherapee's `mul2temp`: the temperature whose blue/red ratio matches, then Green from green/red."""
    r, g, b = (float(v) for v in rgb)
    low, high = MIN_TEMP, MAX_TEMP
    probe = temp_to_srgb((low + high) / 2, 1.0, observer)
    while high - low > 0.05:
        mid = (low + high) / 2
        probe = temp_to_srgb(mid, 1.0, observer)
        if probe[2] / probe[0] > b / r:
            high = mid
        else:
            low = mid
    temp = (low + high) / 2
    probe = temp_to_srgb(temp, 1.0, observer)
    green = (probe[1] / probe[0]) / (g / r)
    return temp, green


@dataclass(frozen=True)
class CameraModel:
    """A camera's dcraw matrix (XYZ to camera, as in camconst.json or LibRaw's adobe_coeff) and what follows from it."""

    cam_xyz: tuple[tuple[float, float, float], ...]
    cam_rgb: np.ndarray = field(init=False, repr=False, compare=False)
    rgb_cam: np.ndarray = field(init=False, repr=False, compare=False)
    pre_mul: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        cam_rgb = np.asarray(self.cam_xyz, dtype=float) @ XYZ_FROM_SRGB
        sums = cam_rgb.sum(axis=1)
        object.__setattr__(self, "cam_rgb", cam_rgb / sums[:, None])
        object.__setattr__(self, "pre_mul", 1.0 / sums)
        object.__setattr__(self, "rgb_cam", np.linalg.inv(cam_rgb / sums[:, None]))

    @classmethod
    def from_matrix(cls, matrix: object) -> CameraModel:
        """From a 3 x 3 (or LibRaw's 4 x 3) XYZ-to-camera matrix, or nine dcraw integers (scaled by 10000)."""
        values = np.asarray(matrix, dtype=float)
        if values.size == 9 and np.abs(values).max() > 50:
            values = values / 10000.0
        values = values.reshape(-1, 3)[:3]
        return cls(tuple(tuple(float(v) for v in row) for row in values))

    def multipliers(self, temp: float, green: float, observer: int = 2) -> np.ndarray:
        """Camera channel multipliers (R, G, B with G = 1) for a RawTherapee temperature and green."""
        camera = self.cam_rgb @ temp_to_srgb(temp, green, observer)
        mul = self.pre_mul / camera
        return mul / mul[1]

    def temperature(self, multipliers: np.ndarray, observer: int = 2) -> tuple[float, float]:
        """The RawTherapee temperature and green that give these camera multipliers."""
        mul = np.asarray(multipliers, dtype=float)[:3]
        srgb = self.rgb_cam @ (self.pre_mul / mul)
        return srgb_to_temp(srgb, observer)

    def illuminant(self, temp: float, green: float, observer: int = 2) -> np.ndarray:
        """The camera's response to a white surface under that light (R, G, B with G = 1)."""
        mul = self.multipliers(temp, green, observer)
        return (1.0 / mul) / (1.0 / mul[1])


def mired(temp: float) -> float:
    return 1e6 / temp


def from_mired(value: float) -> float:
    return 1e6 / value


def angular_error(a: np.ndarray, b: np.ndarray) -> float:
    """The angle in degrees between two illuminant colours (camera RGB), the usual colour constancy error."""
    va, vb = np.asarray(a, dtype=float)[:3], np.asarray(b, dtype=float)[:3]
    cosine = float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))
    return math.degrees(math.acos(min(1.0, max(-1.0, cosine))))
