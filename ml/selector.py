"""Runtime helpers for the learned solver-arm selector."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import numpy as np

from .dataset import build_arms, extract_features


def load_model(path="reports/ml_dataset/selector_model.json") -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def feature_vector(prob_info: dict, model: dict) -> np.ndarray:
    feats = extract_features(prob_info)
    return np.array([float(feats.get(k, 0.0)) for k in model["feature_names"]], dtype=np.float64)


def predict_arm_scores(prob_info: dict, model: dict) -> list[tuple[str, float]]:
    x = feature_vector(prob_info, model)
    mean = np.array(model["mean"], dtype=np.float64)
    scale = np.array(model["scale"], dtype=np.float64)
    coef = np.array(model["coef"], dtype=np.float64)
    xs = (x - mean) / scale
    xb = np.concatenate([[1.0], xs])
    pred_log_obj = xb @ coef
    pairs = [(arm, float(math.exp(v))) for arm, v in zip(model["arms"], pred_log_obj)]
    return sorted(pairs, key=lambda p: p[1])


def select_configs(prob_info: dict, model: dict, top_k: int = 1):
    """Return `(arm_name, OuterConfig)` pairs selected by the model."""
    ranked = [name for name, _score in predict_arm_scores(prob_info, model)]
    arm_map = {name: cfg for name, cfg in build_arms(prob_info, model.get("arm_set", "extended"))}
    out = []
    for name in ranked:
        if name in arm_map:
            out.append((name, deepcopy(arm_map[name])))
        if len(out) >= top_k:
            break
    return out
