// The focus map's colour for a blur value (half-resolution pixels; lower is sharper): green at 0.4 or less,
// yellow around 1, red from 1.8 (ADR 0008's provisional thresholds).
export function blurColour(blur: number, alpha = 0.45): string {
  const t = Math.min(Math.max((blur - 0.4) / 1.4, 0), 1);
  const r = Math.round(t < 0.5 ? 80 + t * 2 * 175 : 255);
  const g = Math.round(t < 0.5 ? 220 : 220 - (t - 0.5) * 2 * 190);
  return `rgba(${r}, ${g}, 60, ${alpha})`;
}
