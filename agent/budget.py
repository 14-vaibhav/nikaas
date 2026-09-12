"""Step / token budget for one loop run (diagram: 'stop - budget low - finish safe').

DECIDE consults this: when the budget is nearly spent it stops adding
expensive routes (HUNT) and lets the loop finish with whatever is already
verified, rather than running dry mid-allocation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this fraction remaining, DECIDE stops choosing HUNT and, if there is
# still no plan, routes STOP so the run ends on verified ground.
LOW_WATER = 0.12


@dataclass
class Budget:
    steps_max: int = 60
    tokens_max: int = 200_000
    steps_used: int = 0
    tokens_used: int = 0

    def alive(self) -> bool:
        return self.steps_used < self.steps_max and self.tokens_used < self.tokens_max

    def fraction_remaining(self) -> float:
        step_frac = 1 - (self.steps_used / self.steps_max if self.steps_max else 0)
        token_frac = 1 - (self.tokens_used / self.tokens_max if self.tokens_max else 0)
        return max(0.0, min(step_frac, token_frac))

    def low(self) -> bool:
        return self.fraction_remaining() < LOW_WATER

    def spend(self, steps: int = 1, tokens: int = 0) -> None:
        self.steps_used += steps
        self.tokens_used += tokens

    def as_dict(self) -> dict:
        return {
            "steps_used": self.steps_used,
            "steps_max": self.steps_max,
            "tokens_used": self.tokens_used,
            "tokens_max": self.tokens_max,
            "fraction_remaining": round(self.fraction_remaining(), 3),
        }
