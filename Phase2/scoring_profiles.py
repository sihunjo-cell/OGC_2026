"""Named Phase 2 scoring profiles."""

from __future__ import annotations


DEFAULT_SCORING_PROFILE = "baseline"

# FIXME: scoring profile portfolio should be pruned after multi-instance comparison.
DEFAULT_SCORING_PROFILE_PORTFOLIO = (
    "baseline",
    "forced_risk_05",
    "forced_risk_10",
    "forced_risk_20",
    "forced_risk_10_contact_half",
    "forced_risk_20_contact_half",
)

SCORING_PROFILE_PARAM_NAMES = (
    "contact_weight_scale",
    "forced_risk_weight",
    "forced_risk_mode",
    "forced_risk_max_residents",
    "corner_weight_scale",
    "temporal_weight_scale",
    "premarsh_weight_scale",
)


SCORING_PROFILES = {
    "baseline": {
        # FIXME: baseline profile should preserve current behavior.
        "contact_weight_scale": 1.0,
        "forced_risk_weight": 0.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "forced_risk_05": {
        # FIXME: tune forced_risk_weight after multi-instance sweep.
        "contact_weight_scale": 1.0,
        "forced_risk_weight": 0.5,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "forced_risk_10": {
        # FIXME: tune forced_risk_weight after multi-instance sweep.
        "contact_weight_scale": 1.0,
        "forced_risk_weight": 1.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "forced_risk_20": {
        # FIXME: current single-instance ALNS result looked promising, but this must be validated on multiple train instances.
        "contact_weight_scale": 1.0,
        "forced_risk_weight": 2.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "forced_risk_10_contact_half": {
        # FIXME: test whether reducing contact weight improves throughput without hurting objective.
        "contact_weight_scale": 0.5,
        "forced_risk_weight": 1.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "forced_risk_20_contact_half": {
        # FIXME: test whether w=2.0 + lower contact improves ALNS throughput and objective.
        "contact_weight_scale": 0.5,
        "forced_risk_weight": 2.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 6,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
    "urgent_exit_safe": {
        # FIXME: experimental safe profile for reducing EXIT blocking and forced placement.
        "contact_weight_scale": 0.5,
        "forced_risk_weight": 2.0,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 8,
        "corner_weight_scale": 0.75,
        "temporal_weight_scale": 1.25,
        "premarsh_weight_scale": 1.25,
    },
    "compact": {
        # FIXME: compact profile may improve packing but can increase future crane blocking.
        "contact_weight_scale": 1.25,
        "forced_risk_weight": 0.5,
        "forced_risk_mode": "overlap_crane",
        "forced_risk_max_residents": 4,
        "corner_weight_scale": 1.0,
        "temporal_weight_scale": 1.0,
        "premarsh_weight_scale": 1.0,
    },
}


def get_scoring_profile(name: str) -> dict:
    if name not in SCORING_PROFILES:
        raise ValueError(f"Unknown scoring profile: {name}")
    return dict(SCORING_PROFILES[name])


def list_scoring_profiles() -> list[str]:
    return list(SCORING_PROFILES.keys())
