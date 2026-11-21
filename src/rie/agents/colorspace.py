"""Small, fast colour conversions on NumPy arrays: sRGB encoding, linear sRGB to XYZ and CIELAB (D65), CAT16.

colour-science holds the reference implementations; these few are written out so agents can run them on whole
images without its two-second import.
"""

from __future__ import annotations

import numpy as np

XYZ_FROM_LINEAR_SRGB = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
LINEAR_SRGB_FROM_XYZ = np.linalg.inv(XYZ_FROM_LINEAR_SRGB)
D65_WHITE = XYZ_FROM_LINEAR_SRGB @ np.ones(3)
LUMA = XYZ_FROM_LINEAR_SRGB[1]
# CAT16 (Li et al. 2017): XYZ to the sharpened cone space where von Kries adaptation is done.
M_CAT16 = np.array(
    [
        [0.401288, 0.650173, -0.051461],
        [-0.250268, 1.204414, 0.045854],
        [-0.002079, 0.048952, 0.953127],
    ]
)


def decode_srgb(values: np.ndarray) -> np.ndarray:
    """8-bit or 0-1 sRGB to linear 0-1 (float32)."""
    raw = np.asarray(values)
    a = raw.astype(np.float32)
    if raw.dtype == np.uint8 or a.max(initial=0) > 1.0:
        a /= 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4).astype(np.float32)


def encode_srgb(values: np.ndarray) -> np.ndarray:
    a = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - 0.055).astype(np.float32)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Relative luminance Y of linear sRGB."""
    return rgb @ LUMA.astype(np.float32)


def lightness(y: np.ndarray | float) -> np.ndarray:
    """CIE L* (0-100) of a relative luminance."""
    y = np.asarray(y, dtype=np.float64)
    f = np.where(y > (6 / 29) ** 3, np.cbrt(y), y / (3 * (6 / 29) ** 2) + 4 / 29)
    return 116 * f - 16


def luminance_of_lightness(lstar: float) -> float:
    f = (lstar + 16) / 116
    return float(f**3 if f > 6 / 29 else 3 * (6 / 29) ** 2 * (f - 4 / 29))


def lab(rgb: np.ndarray) -> np.ndarray:
    """CIELAB (D65 white) of linear sRGB, on the last axis."""
    xyz = np.asarray(rgb, dtype=np.float64) @ XYZ_FROM_LINEAR_SRGB.T / D65_WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(np.maximum(xyz, 0)), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    out = np.empty_like(f)
    out[..., 0] = 116 * f[..., 1] - 16
    out[..., 1] = 500 * (f[..., 0] - f[..., 1])
    out[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return out


def hue_chroma(lab_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hue angle (degrees, 0-360) and chroma of CIELAB values."""
    a, b = lab_values[..., 1], lab_values[..., 2]
    return np.degrees(np.arctan2(b, a)) % 360, np.hypot(a, b)


def xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1 - x - y) / y])


def adapted_white(illuminant_xyz: np.ndarray, reference_xyz: np.ndarray, degree: float) -> np.ndarray:
    """The light a white balance should neutralize so the illuminant keeps `1 - degree` of its cast (CAT16).

    With degree D, CAT16 sees the illuminant's white as `D * reference + (1 - D) * illuminant` in its cone space.
    Balancing for the returned white renders the illuminant's white that way; D = 1 neutralizes it fully.
    """
    lms_w = M_CAT16 @ (illuminant_xyz / illuminant_xyz[1])
    lms_r = M_CAT16 @ (reference_xyz / reference_xyz[1])
    seen = degree * lms_r + (1 - degree) * lms_w
    target = lms_r * lms_w / seen
    xyz = np.linalg.solve(M_CAT16, target)
    return xyz / xyz[1]
