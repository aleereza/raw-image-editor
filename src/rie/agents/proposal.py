"""Proposals and confidence routing (plan §4).

An agent returns a `Proposal`: recipe parameters, a confidence from 0 to 1, a one-sentence rationale, the same
parameters as shot (so the photo can be reset, or corrected only halfway), and the measurements behind it. The
confidence picks one of three bands:

| Confidence | What happens |
|---|---|
| at least `apply_at` (0.8 unless the agent says otherwise) | applied silently |
| at least `conservative_at` (0.5) | applied halfway and queued for review |
| below | left as shot, queued for review with the reason |

An agent that abstains (nothing to measure, such as straightening a photo without lines) leaves the photo as shot
without queueing it. Halfway means half the change in the parameter's natural scale: mired for colour temperature,
a ratio for green, plain numbers for the rest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

APPLY = 0.8
CONSERVATIVE = 0.5

type Band = Literal["apply", "conservative", "as_shot"]
type Params = dict[str, dict[str, Any]]


def _blend(group: str, name: str, start: Any, end: Any, fraction: float) -> Any:
    if fraction >= 1 or start == end:
        return end
    if fraction <= 0:
        return start
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int | float):
        return end if fraction >= 0.5 else start
    if not isinstance(end, int | float):
        return end if fraction >= 0.5 else start
    if (group, name) == ("white_balance", "temperature_k"):
        mired = 1e6 / start + fraction * (1e6 / end - 1e6 / start)
        return 1e6 / mired
    if (group, name) == ("white_balance", "tint"):
        return math.exp(math.log(start) + fraction * (math.log(end) - math.log(start)))
    value = start + fraction * (end - start)
    return round(value) if isinstance(start, int) and isinstance(end, int) else value


def blend(start: Params, end: Params, fraction: float) -> Params:
    """Parameters `fraction` of the way from `start` (as shot) to `end` (proposed); 0 gives `start`."""
    out: Params = {}
    for group in dict.fromkeys([*start, *end]):
        a, b = start.get(group, {}), end.get(group, {})
        values = {}
        for name in dict.fromkeys([*a, *b]):
            if name not in b:
                if fraction < 1 and name in a:
                    values[name] = a[name]
                continue
            if name not in a:
                if fraction >= 0.5:
                    values[name] = b[name]
                continue
            values[name] = _blend(group, name, a[name], b[name], fraction)
        if values:
            out[group] = values
    return out


@dataclass
class Proposal:
    agent: str  # name@version, such as "wb@1"
    params: Params  # what the agent proposes
    confidence: float
    rationale: str  # one sentence, for the reason chip
    as_shot: Params = field(default_factory=dict)  # the same parameters as shot, numerically where possible
    evidence: dict[str, Any] = field(default_factory=dict)  # measurements behind the proposal
    flags: list[str] = field(default_factory=list)  # things the owner should know, such as "deliberate tilt?"
    abstain: bool = False  # nothing to measure: as shot, without review
    apply_at: float = APPLY
    conservative_at: float = CONSERVATIVE

    @property
    def changes(self) -> bool:
        return blend(self.as_shot, self.params, 1.0) != blend(self.as_shot, self.as_shot, 1.0)

    @property
    def band(self) -> Band:
        if self.abstain:
            return "as_shot"
        if self.confidence >= self.apply_at:
            return "apply"
        if self.confidence >= self.conservative_at:
            return "conservative"
        return "as_shot"

    def route(self) -> Routed:
        band = self.band
        if band == "apply":
            applied, fraction = self.params, 1.0
        elif band == "conservative":
            applied, fraction = blend(self.as_shot, self.params, 0.5), 0.5
        else:
            applied, fraction = {}, 0.0
        review = not self.abstain and (bool(self.flags) or (band != "apply" and self.changes))
        return Routed(self, band, applied, fraction, review)

    def to_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "params": self.params,
            "as_shot": self.as_shot,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "evidence": self.evidence,
            "flags": self.flags,
            "abstain": self.abstain,
            "band": self.band,
        }


@dataclass
class Routed:
    proposal: Proposal
    band: Band
    applied: Params  # the parameters that go into the recipe (empty: as shot)
    fraction: float  # 1 applied, 0.5 halfway, 0 as shot
    review: bool

    @property
    def reason(self) -> str:
        p = self.proposal
        if self.band == "conservative":
            return f"applied halfway ({round(p.confidence * 100)} % sure): {p.rationale}"
        if self.band == "as_shot" and not p.abstain and p.changes:
            return f"left as shot ({round(p.confidence * 100)} % sure): {p.rationale}"
        return p.rationale
