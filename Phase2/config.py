"""Phase 2 configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from .scoring_profiles import (
    DEFAULT_SCORING_PROFILE,
    SCORING_PROFILE_PARAM_NAMES,
    get_scoring_profile,
)


@dataclass
class Phase2Config:
    phase2_variant_topks: tuple = (4, 8, 16,)  # FIXME: Phase 2 top-k sweep should be pruned after multi-instance comparison.
    phase2_scoring_profiles: tuple | None = None  # FIXME: scoring profile portfolio should be pruned after multi-instance comparison.

    # Strategy toggles
    improve_mode: str = "off"           # "off" | "jostle_2exchange"
    forcing_mode: str = "empty_bay"     # "empty_bay" | "earliest_slot"

    # Placement order
    order_mode: str = "area"            # "area" | "mst"
    lam1: float = 1.0

    # Placement heuristic weights
    w_ct: float = 1.0  # FIXME: base contact weight should be tuned with scoring profile sweep.
    w_cn: float = 0.01  # FIXME: base corner weight should be tuned with scoring profile sweep.
    w_tp: float = 0.1  # FIXME: base temporal weight should be tuned with scoring profile sweep.
    w_pm: float = 1.0  # FIXME: base premarsh weight should be tuned with scoring profile sweep.
    K: float = 4.0  # FIXME: S-curve sharpness should be tuned with scoring profile sweep.
    contact_exact_top_k: int = 8  # FIXME: exact re-ranking breadth should be tuned with multi-instance comparison.
    scoring_profile: str = DEFAULT_SCORING_PROFILE  # FIXME: tune default scoring profile after multi-instance sweep.
    contact_weight_scale: float | None = None  # FIXME: contact scale is experimental and should be tuned via scoring profiles.
    forced_risk_weight: float | None = None  # FIXME: forced-risk weight is experimental and should be tuned via scoring profiles.
    forced_risk_mode: str | None = None  # FIXME: forced-risk mode is experimental and should be tuned via scoring profiles.
    forced_risk_max_residents: int | None = None  # FIXME: forced-risk resident cap is experimental and should be tuned via scoring profiles.
    corner_weight_scale: float | None = None  # FIXME: corner scale is experimental and should be tuned via scoring profiles.
    temporal_weight_scale: float | None = None  # FIXME: temporal scale is experimental and should be tuned via scoring profiles.
    premarsh_weight_scale: float | None = None  # FIXME: premarsh scale is experimental and should be tuned via scoring profiles.

    # Improve
    improve_rounds: int = 2

    # Repair
    max_repair_passes: int = 2

    _resolving_scoring_profile: bool = field(init=False, repr=False, default=False)
    _resolved_scoring_params: dict = field(init=False, repr=False, default_factory=dict)
    _scoring_overrides: dict = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scoring_overrides", {})
        for name in SCORING_PROFILE_PARAM_NAMES:
            value = getattr(self, name)
            if value is not None:
                self._scoring_overrides[name] = value
        self.resolve_scoring_profile()

    def __setattr__(self, name, value) -> None:
        object.__setattr__(self, name, value)
        if name not in SCORING_PROFILE_PARAM_NAMES:
            return
        if getattr(self, "_resolving_scoring_profile", False):
            return
        overrides = getattr(self, "_scoring_overrides", None)
        if overrides is None:
            return
        if value is None:
            overrides.pop(name, None)
        else:
            overrides[name] = value

    def resolve_scoring_profile(self) -> dict:
        profile = get_scoring_profile(self.scoring_profile)
        resolved = {}
        object.__setattr__(self, "_resolving_scoring_profile", True)
        try:
            for name in SCORING_PROFILE_PARAM_NAMES:
                value = self._scoring_overrides.get(name, profile[name])
                object.__setattr__(self, name, value)
                resolved[name] = value
        finally:
            object.__setattr__(self, "_resolving_scoring_profile", False)
        object.__setattr__(self, "_resolved_scoring_params", dict(resolved))
        return dict(resolved)

    def scoring_params(self) -> dict:
        if not self._resolved_scoring_params:
            return self.resolve_scoring_profile()
        return dict(self._resolved_scoring_params)
