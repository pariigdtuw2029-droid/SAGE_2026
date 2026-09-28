"""
Model inference wiring (Member 2).

Runs the SAGE artifacts over a cleaned burn-in frame and produces the rows that
populate `predictions`, `risk_assessments` and `explanations`:

  • Module A — `models/anomaly_pipeline.joblib`      anomaly risk + QA decision
               (lot-relative + multivariate IsolationForest + drift severity,
                weights/thresholds already tuned on the val split *inside* the
                fitted PipelineConfig). Classes are re-created in `model_compat`
                because the artifact was pickled from a notebook `__main__`.
  • Module B — `models/drift_prediction_models.joblib`  168h drift forecast
               (tuned RF + GB + Ridge meta-stack, quantile intervals, and the
                `safety_slope` thresholds measured on Safe TRAIN rows only).
  • Module C — reliability composition (no artifact; the notebook's tuned
               weights, tier cuts and risk floors are reproduced here, read
               from `models/config.json` so a re-tune never means editing code).
  • Explainability — `models/anomaly_shap_bundle.joblib`, a *portable* plain-dict
               bundle (per-group IsolationForests + SHAP backgrounds + imputation
               medians) so SHAP can be computed without the custom classes.

Notebook-vs-backend deltas, deliberately chosen and documented:

  • Normalization bounds for Predicted_Drift_Score / Uncertainty_Score are
    computed from the 99th percentile of the rows being ingested rather than of
    the fixed train+val split. Ingesting the full dataset reproduces the
    notebook; ingesting a slice is a documented approximation (scores are
    batch-relative, never absolute).
  • Module B does not drop rows with missing 96h/168h targets — the backend must
    score every component it is handed, not just the ones the notebook kept.

Every load/predict is defensive: if an artifact cannot be loaded (missing
dependency, version drift, file absent), the stack degrades to a pure
statistical fallback (lot z-score + early-drift slope extrapolation) so
ingestion never hard-fails.
"""

import hashlib
import json
from pathlib import Path
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Artifacts (names are owned by the notebooks — keep them in one place)
# ---------------------------------------------------------------------------

ANOMALY_ARTIFACT = "anomaly_pipeline.joblib"
ANOMALY_ARTIFACT_LEGACY = "pipeline.joblib"
DRIFT_ARTIFACT = "drift_prediction_models.joblib"
DRIFT_ARTIFACT_LEGACY = "drift_prediction_model.joblib"
SHAP_BUNDLE_ARTIFACT = "anomaly_shap_bundle.joblib"
CONFIG_ARTIFACT = "config.json"
RELIABILITY_BUNDLE_ARTIFACT = "reliability_bundle.joblib"

MODEL_VERSION = "sage-1.1"          # reported on every stored row
FALLBACK_MODEL_VERSION = "fallback-stats"

CHECKPOINTS: List[str] = ["0h", "24h", "96h", "168h"]
PARAMETERS: List[str] = ["Leakage", "Resistance", "Vth"]
DRIFT_HORIZON_HOURS = 168.0

# ---------------------------------------------------------------------------
# Module C — defaults are the values the current notebooks tuned on val.
# `models/config.json["module_c"]` overrides any of them at runtime.
# ---------------------------------------------------------------------------

WEIGHT_ANOMALY = 0.278
WEIGHT_PREDICTED_DRIFT = 0.670
WEIGHT_UNCERTAINTY = 0.052

ABSOLUTE_FAIL_RISK_FLOOR = 65.0      # notebook: ABSOLUTE_FAIL_RISK_FLOOR
SLOPE_REJECT_RISK_FLOOR = 65.0       # notebook: SLOPE_REJECT_RISK_FLOOR

RISK_SAFE_MAX = 21.8                 # Safe < 21.8 ≤ Borderline < 65.0 ≤ High-Risk
RISK_BORDERLINE_MAX = 65.0

# Uncertainty = 0.6 · aleatoric (quantile-interval width) + 0.4 · epistemic
# (RF-vs-GB disagreement), each normalized by its 99th percentile and capped.
UNCERTAINTY_ALEATORIC_WEIGHT = 0.6
UNCERTAINTY_EPISTEMIC_WEIGHT = 0.4
UNCERTAINTY_RATIO_CAP = 1.5

# Which parameter each proxy stress is expected to hit hardest (reliability notebook).
STRESS_PHYSICS: Dict[str, str] = {
    "Thermal-High": "Leakage",
    "Radiation-Low": "Vth",
    "Cycling-Medium": "Resistance",
}

_SHAP_FEATURE_RE = re.compile(r"([A-Za-z0-9_]+)\(\s*([+-]?\d+(?:\.\d+)?)\s*\)")

_ARTIFACT_DIR = None  # resolved lazily relative to this package


def _artifact_path(name: str):
    global _ARTIFACT_DIR
    if _ARTIFACT_DIR is None:
        import os
        from pathlib import Path
        _ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "models"
        if not _ARTIFACT_DIR.exists():
            _ARTIFACT_DIR = Path(os.getenv("SAGE_MODEL_DIR", "models"))
    return _ARTIFACT_DIR / name


def _first_existing(*names: str):
    """First artifact that exists, preferring the current name over the legacy one."""
    for name in names:
        path = _artifact_path(name)
        if path.exists():
            return path
    return _artifact_path(names[0])


def artifact_dir() -> str:
    """Directory the artifacts are resolved from (`backend/models`, or $SAGE_MODEL_DIR)."""
    return str(_artifact_path("").resolve())


def compute_file_sha256(path: Union[str, Path]) -> str:
    """
    Compute lowercase hexadecimal SHA-256 hash of a file on disk in binary chunks.
    Raises FileNotFoundError if the file does not exist.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Artifact not found: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def get_active_artifact_hashes() -> Dict[str, str]:
    """
    Compute and return SHA-256 hashes of the four active model/config artifacts:
    1. anomaly_pipeline.joblib
    2. drift_prediction_models.joblib
    3. anomaly_shap_bundle.joblib
    4. config.json
    """
    artifacts = {
        "anomaly_pipeline.joblib": _first_existing(ANOMALY_ARTIFACT, ANOMALY_ARTIFACT_LEGACY),
        "drift_prediction_models.joblib": _first_existing(DRIFT_ARTIFACT, DRIFT_ARTIFACT_LEGACY),
        "anomaly_shap_bundle.joblib": _artifact_path(SHAP_BUNDLE_ARTIFACT),
        "config.json": _artifact_path(CONFIG_ARTIFACT),
    }
    hashes = {}
    for key, path in artifacts.items():
        hashes[key] = compute_file_sha256(path)
    return hashes


def describe_stack() -> dict:
    """
    What this process would actually run: which artifacts are present, Module A's
    tuned layer weights and decision thresholds as fitted, Module B's safety
    slopes, and the effective Module C constants.

    Surfaced by `python -m database stack` — the fastest way to prove which model
    version a demo is running, and to catch a silently degraded load.
    """
    out: dict = {
        "stack_version": MODEL_VERSION,
        "artifact_dir": artifact_dir(),
        "artifacts": {},
        "module_a": {"available": False, "config": None, "error": None},
        "module_a_shap_bundle": {"available": False, "groups": []},
        "module_b": {"available": False, "safety_slope": None, "error": None},
        "module_c": reliability_config(),
        "explainability": explainability_config(),
    }

    for name in (ANOMALY_ARTIFACT, ANOMALY_ARTIFACT_LEGACY, SHAP_BUNDLE_ARTIFACT,
                 DRIFT_ARTIFACT, DRIFT_ARTIFACT_LEGACY, CONFIG_ARTIFACT, "metadata.json"):
        path = _artifact_path(name)
        out["artifacts"][name] = {
            "present": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
        }

    try:
        pipeline = load_anomaly_pipeline()
        config = getattr(pipeline, "config", None)
        if config is not None:
            dumped = getattr(config, "to_dict", None)
            out["module_a"]["config"] = dumped() if callable(dumped) else vars(config)
        out["module_a"]["available"] = True
    except Exception as exc:  # noqa: BLE001
        out["module_a"]["error"] = str(exc)

    try:
        bundle = load_shap_bundle()
        out["module_a_shap_bundle"] = {
            "available": True,
            "groups": sorted(str(k) for k in bundle.get("iforest_models", {})),
        }
    except Exception as exc:  # noqa: BLE001
        out["module_a_shap_bundle"]["error"] = str(exc)

    try:
        drift = load_drift_bundle()
        slopes = drift.get("safety_slope")
        out["module_b"] = {
            "available": True,
            "safety_slope": {str(k): float(v) for k, v in slopes.items()} if slopes else None,
            "error": None if slopes else "artifact has no 'safety_slope' (pre-v5.1)",
        }
    except Exception as exc:  # noqa: BLE001
        out["module_b"]["error"] = str(exc)

    return out


@lru_cache(maxsize=1)
def model_config() -> dict:
    """`models/config.json` (tuned constants + provenance), {} when absent."""
    path = _artifact_path(CONFIG_ARTIFACT)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a malformed config must not break ingestion
        return {}


@lru_cache(maxsize=1)
def load_reliability_bundle() -> Optional[dict]:
    """`models/reliability_bundle.joblib` (tuned Module C bundle), None when absent."""
    path = _artifact_path(RELIABILITY_BUNDLE_ARTIFACT)
    if not path.exists():
        return None
    try:
        import joblib
        return joblib.load(path)
    except Exception:
        return None


@lru_cache(maxsize=1)
def reliability_config() -> dict:
    """
    Effective Module C constants: config.json["module_c"] or reliability_bundle.joblib over code defaults.
    Cached — a re-tune requires a process restart (or `reliability_config.cache_clear()`).
    """
    module_c = model_config().get("module_c", {}) or {}
    rel_bundle = load_reliability_bundle() or {}

    def num(key: str, default: float) -> float:
        if key in module_c:
            try:
                return float(module_c[key])
            except (TypeError, ValueError):
                pass
        if key in rel_bundle:
            try:
                return float(rel_bundle[key])
            except (TypeError, ValueError):
                pass
        return default

    return {
        "weight_anomaly": num("weight_anomaly", WEIGHT_ANOMALY),
        "weight_predicted_drift": num("weight_predicted_drift", WEIGHT_PREDICTED_DRIFT),
        "weight_uncertainty": num("weight_uncertainty", WEIGHT_UNCERTAINTY),
        "absolute_fail_risk_floor": num("absolute_fail_risk_floor", ABSOLUTE_FAIL_RISK_FLOOR),
        "slope_reject_risk_floor": num("slope_reject_risk_floor", SLOPE_REJECT_RISK_FLOOR),
        "risk_safe_max": num("risk_safe_max", RISK_SAFE_MAX),
        "risk_borderline_max": num("risk_borderline_max", RISK_BORDERLINE_MAX),
        "uncertainty_aleatoric_weight": num("uncertainty_aleatoric_weight", UNCERTAINTY_ALEATORIC_WEIGHT),
        "uncertainty_epistemic_weight": num("uncertainty_epistemic_weight", UNCERTAINTY_EPISTEMIC_WEIGHT),
        "uncertainty_ratio_cap": num("uncertainty_ratio_cap", UNCERTAINTY_RATIO_CAP),
    }


@lru_cache(maxsize=1)
def _explainability_defaults() -> dict:
    """SHAP budget settings from config.json (disabled by default: it is slow)."""
    cfg = model_config().get("explainability", {}) or {}

    def val(key: str, default):
        try:
            return type(default)(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": val("enabled", False),
        "max_rows": val("max_rows", 50),
        "top_k": val("top_k", 2),
    }


_EXPLAIN_OVERRIDE: Dict[str, object] = {}


def explainability_config() -> dict:
    """Effective SHAP settings (config.json, overridden by `set_explainability_budget`)."""
    cfg = dict(_explainability_defaults())
    cfg.update(_EXPLAIN_OVERRIDE)
    return cfg


def set_explainability_budget(max_rows: Optional[int] = None) -> dict:
    """
    Force the SHAP pass on for this process, optionally capped at `max_rows`
    (0 or None = no cap). Used by `python -m database ingest <csv> --explain N`.
    """
    _EXPLAIN_OVERRIDE["enabled"] = True
    if max_rows is not None:
        _EXPLAIN_OVERRIDE["max_rows"] = int(max_rows)
    return explainability_config()


# ---------------------------------------------------------------------------
# Module A — anomaly pipeline
# ---------------------------------------------------------------------------

_NOTEBOOK_CLASS_NAMES = [
    "AnomalyPipeline", "PipelineConfig", "LotMedianImputer", "LotRelativeScorer",
    "MultivariateAnomalyScorer", "DriftSeverityScorer", "AbsoluteSpecLayer",
    "add_drift_features", "validate_schema", "SchemaError",
]


def _register_notebook_classes():
    """Publish the model_compat classes under __main__ so pickle can resolve them."""
    import __main__
    import sys
    import database.model_compat as mc
    for n in _NOTEBOOK_CLASS_NAMES:
        try:
            setattr(__main__, n, getattr(mc, n))
        except AttributeError:  # a renamed notebook helper shouldn't sink the load
            continue
    sys.modules.setdefault("__main__", __main__)


@lru_cache(maxsize=1)
def load_anomaly_pipeline():
    """Load Module A. Raises RuntimeError if unavailable."""
    path = _first_existing(ANOMALY_ARTIFACT, ANOMALY_ARTIFACT_LEGACY)
    if not path.exists():
        raise RuntimeError(f"Module A artifact not found: {path}")
    import joblib
    _register_notebook_classes()
    try:
        return joblib.load(str(path))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        raise RuntimeError(f"Failed to load Module A pipeline ({path.name}): {exc}") from exc


# ---------------------------------------------------------------------------
# Module B — drift forecast bundle
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_drift_bundle():
    """Load Module B, shimming old sklearn references in the pickle."""
    path = _first_existing(DRIFT_ARTIFACT, DRIFT_ARTIFACT_LEGACY)
    if not path.exists():
        raise RuntimeError(f"Module B artifact not found: {path}")
    import sys
    try:
        # Older sklearn pickles reference the bare `_loss` module.
        import sklearn._loss._loss as real_loss  # noqa: PLC0415
        sys.modules.setdefault("_loss", real_loss)
    except Exception:  # noqa: BLE001 — newer sklearn doesn't need the shim
        pass
    import joblib
    try:
        bundle = joblib.load(str(path))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load Module B drift bundle ({path.name}): {exc}") from exc

    # Newer sklearn removed SimpleImputer._fill_dtype (older pickles reference it).
    imputer = bundle.get("imputer")
    if imputer is not None and not hasattr(imputer, "_fill_dtype"):
        stats = getattr(imputer, "statistics_", None)
        imputer._fill_dtype = stats.dtype if stats is not None else None
        bundle["imputer"] = imputer
    return bundle


def _elapsed_hours(df: pd.DataFrame, checkpoint: str, nominal: float) -> pd.Series:
    """
    Hours between the 0h anchor and `checkpoint` for every row, falling back to
    the nominal schedule when the timestamps are missing or unparseable.
    """
    base = df.get("Timestamp_0h_ts")
    col = f"Timestamp_{checkpoint}_ts"
    if base is None or col not in df.columns:
        return pd.Series(nominal, index=df.index, dtype=float)
    delta = (pd.to_datetime(df[col], errors="coerce") - pd.to_datetime(base, errors="coerce"))
    return (delta.dt.total_seconds() / 3600.0).fillna(nominal)


def _drift_feature_matrix(df: pd.DataFrame, bundle: dict) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reproduce the drift notebook's feature engineering exactly:
    impute → early-slope features → one-hot stress/part-type → reindex to the
    training column order. Returns (X, imputed_frame) — the frame is reused for
    SHAP and for the 24h baselines needed to reconstruct forecasts.
    """
    df = df.copy()
    feature_cols_raw = list(bundle["feature_cols_raw"])

    # same sensor-fault handling as the training notebook
    reading_cols = [f"{m}_{cp}" for m in PARAMETERS for cp in CHECKPOINTS]
    for col in reading_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[df[col] < 0, col] = np.nan

    df["elapsed_24h"] = _elapsed_hours(df, "24h", 24.0)
    df["elapsed_96h"] = _elapsed_hours(df, "96h", 96.0)
    df["elapsed_168h"] = _elapsed_hours(df, "168h", 168.0)

    for col in feature_cols_raw:
        if col not in df.columns:
            df[col] = np.nan
    df.loc[:, feature_cols_raw] = bundle["imputer"].transform(df[feature_cols_raw])

    for m in PARAMETERS:
        if f"{m}_24h" in df.columns and f"{m}_0h" in df.columns:
            df[f"{m}_slope"] = (df[f"{m}_24h"] - df[f"{m}_0h"]) / df["elapsed_24h"].replace(0, 1e-9)

    feature_cols_num = bundle.get("feature_cols_num")
    if not feature_cols_num:
        feature_cols_num = feature_cols_raw + ["Stress_Level", "elapsed_24h"] + [
            f"{m}_slope" for m in PARAMETERS if f"{m}_slope" in df.columns
        ]
    for c in feature_cols_num:
        if c not in df.columns:
            df[c] = 0.0

    cat_cols = bundle.get("cat_cols", ["Proxy_Stress", "Part_Type"])
    X = pd.get_dummies(
        df[feature_cols_num + cat_cols],
        columns=cat_cols,
    )
    target_columns = bundle.get("train_columns", bundle.get("feature_columns"))
    X = X.reindex(columns=target_columns, fill_value=0)
    return X, df


def _reconstruct_drift(raw: np.ndarray, df: pd.DataFrame) -> pd.DataFrame:
    """Map model outputs back to physical units (log1p for Leakage, delta for the rest)."""
    out = pd.DataFrame(index=df.index)
    n_out = raw.shape[1] if len(raw.shape) > 1 else 1
    if n_out == 2:
        out["Leakage_168h"] = np.expm1(raw[:, 1])
    else:
        out["Leakage_168h"] = np.expm1(raw[:, 1])
        if n_out >= 4 and "Resistance_24h" in df.columns:
            out["Resistance_168h"] = df["Resistance_24h"].values + raw[:, 3]
        if n_out >= 6 and "Vth_24h" in df.columns:
            out["Vth_168h"] = df["Vth_24h"].values + raw[:, 5]
    return out


def run_module_b(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Forecast the 168h checkpoint for Leakage/Resistance/Vth and compute
    Predicted_Drift_Score, Uncertainty_Score and Slope_Reject_Flag (mirrors
    reliability_index.ipynb v5.1).

    Returns (frame, warnings) keyed by Component_ID with:

        Pred_<P>_168h, Interval_Low_<P>, Interval_High_<P>, Interval_Width_<P>,
        Predicted_Drift_<P>, Disagreement_<P>, Predicted_Drift_Score,
        Uncertainty_Score, Slope_Reject_Flag, model_version
    """
    warnings: List[str] = []
    cfg = reliability_config()
    bundle = load_drift_bundle()

    X, df = _drift_feature_matrix(df, bundle)

    rf_pred = bundle["rf"].predict(X)
    gb_pred = bundle["gb"].predict(X)
    stacked_raw = bundle["meta_model"].predict(np.hstack([rf_pred, gb_pred]))
    lower_raw = bundle["quantile_lower"].predict(X)
    upper_raw = bundle["quantile_upper"].predict(X)

    # The notebook clips quantile crossing before reconstructing.
    lower_raw, upper_raw = np.minimum(lower_raw, upper_raw), np.maximum(lower_raw, upper_raw)

    point = _reconstruct_drift(np.asarray(stacked_raw), df)
    lower = _reconstruct_drift(np.asarray(lower_raw), df)
    upper = _reconstruct_drift(np.asarray(upper_raw), df)
    rf_point = _reconstruct_drift(np.asarray(rf_pred), df)
    gb_point = _reconstruct_drift(np.asarray(gb_pred), df)

    active_params = [m for m in PARAMETERS if f"{m}_168h" in point.columns and f"{m}_24h" in df.columns]
    result = pd.DataFrame({"Component_ID": df["Component_ID"].values})
    for m in active_params:
        result[f"Pred_{m}_168h"] = point[f"{m}_168h"].values
        result[f"Interval_Low_{m}"] = lower[f"{m}_168h"].values
        result[f"Interval_High_{m}"] = upper[f"{m}_168h"].values
        result[f"Interval_Width_{m}"] = (
            upper[f"{m}_168h"] - lower[f"{m}_168h"]
        ).clip(lower=0).values
        result[f"Predicted_Drift_{m}"] = (
            (point[f"{m}_168h"] - df[f"{m}_24h"]).abs() / df[f"{m}_24h"].abs().replace(0, 1e-9)
        ).values
        result[f"Disagreement_{m}"] = (rf_point[f"{m}_168h"] - gb_point[f"{m}_168h"]).abs().values

    # --- Predicted_Drift_Score: max relative drift, normalized to its bound ---
    rel_bundle = load_reliability_bundle()
    pred_drift_cols = [f"Predicted_Drift_{m}" for m in active_params]
    if pred_drift_cols:
        severity = result[pred_drift_cols].max(axis=1)
        if rel_bundle and "drift_score_bound" in rel_bundle:
            bound = float(rel_bundle["drift_score_bound"])
        else:
            bound = float(severity.quantile(0.99)) if len(severity) else 1.0
        result["Predicted_Drift_Score"] = (severity.clip(upper=bound) / max(bound, 1e-9) * 100).clip(0, 100).values
    else:
        result["Predicted_Drift_Score"] = 0.0

    # --- Uncertainty_Score: 0.6 · aleatoric (interval width) + 0.4 · epistemic (RF/GB gap) ---
    iw_cols = [f"Interval_Width_{m}" for m in active_params]
    dis_cols = [f"Disagreement_{m}" for m in active_params]
    if iw_cols:
        iw = result[iw_cols]
        iw_bounds = rel_bundle.get("iw_bounds") if rel_bundle else None
        if iw_bounds:
            iw_bounds_s = pd.Series({c: iw_bounds.get(c, iw[c].quantile(0.99)) for c in iw_cols}).clip(lower=1e-9)
        else:
            iw_bounds_s = iw.quantile(0.99).clip(lower=1e-9)
        aleatoric = ((iw / iw_bounds_s).clip(upper=cfg["uncertainty_ratio_cap"]).max(axis=1) * 100).clip(0, 100)

        dis = result[dis_cols]
        dis_bounds = rel_bundle.get("dis_bounds") if rel_bundle else None
        if dis_bounds:
            dis_bounds_s = pd.Series({c: dis_bounds.get(c, dis[c].quantile(0.99)) for c in dis_cols}).clip(lower=1e-9)
        else:
            dis_bounds_s = dis.quantile(0.99).clip(lower=1e-9)
        epistemic = ((dis / dis_bounds_s).clip(upper=cfg["uncertainty_ratio_cap"]).max(axis=1) * 100).clip(0, 100)

        result["Uncertainty_Score"] = (
            cfg["uncertainty_aleatoric_weight"] * aleatoric
            + cfg["uncertainty_epistemic_weight"] * epistemic
        ).clip(0, 100).round(1).values
    else:
        result["Uncertainty_Score"] = 0.0

    # --- Slope_Reject_Flag: predicted drift rate vs the Safe-population slope ---
    safety_slope = bundle.get("safety_slope")
    if not safety_slope:
        warnings.append(
            "Module B bundle has no 'safety_slope' (pre-v5.1 artifact); "
            "Slope_Reject_Flag left False."
        )
        result["Slope_Reject_Flag"] = False
    else:
        denom = (df["elapsed_168h"] - df["elapsed_24h"]).replace(0, 1e-9).values
        flag = np.zeros(len(result), dtype=bool)
        for m in active_params:
            if m in safety_slope:
                rate = np.abs(
                    (result[f"Pred_{m}_168h"].values - df[f"{m}_24h"].values) / denom
                )
                try:
                    flag |= rate > float(safety_slope[m])
                except (KeyError, TypeError, ValueError):
                    warnings.append(f"Module B safety_slope is missing '{m}'; that parameter is not slope-checked.")
        result["Slope_Reject_Flag"] = flag

    result["model_version"] = MODEL_VERSION
    return result, warnings


# ---------------------------------------------------------------------------
# Module C — reliability composition + decisions
# ---------------------------------------------------------------------------

def compute_reliability(anomaly: pd.DataFrame, drift: pd.DataFrame) -> pd.DataFrame:
    """
    Fuse Module A (anomaly) + Module B (drift) into a per-component risk record,
    exactly as reliability_index.ipynb does:

        Space_Risk_Score = w_a·anomaly + w_d·predicted_drift + w_u·uncertainty
        raised to the absolute-spec floor / safety-slope floor, then tiered.

    Inputs are keyed by Component_ID; the output keeps both the notebook's names
    (Space_Risk_Score / Space_Reliability_Index / Reliability_Tier) and the API
    vocabulary (risk_score / reliability_index / reliability_tier, risk_level).
    """
    cfg = reliability_config()
    merged = anomaly.merge(drift, on="Component_ID", how="left", suffixes=("", "_b"))

    risk = (
        cfg["weight_anomaly"] * merged["Anomaly_Risk_Score"]
        + cfg["weight_predicted_drift"] * merged["Predicted_Drift_Score"].fillna(0.0)
        + cfg["weight_uncertainty"] * merged["Uncertainty_Score"].fillna(0.0)
    )
    risk = np.where(
        merged["Absolute_Spec_Fail"].fillna(0).astype(int) == 1,
        np.maximum(risk, cfg["absolute_fail_risk_floor"]),
        risk,
    )
    slope_flag = merged.get("Slope_Reject_Flag")
    if slope_flag is not None:
        risk = np.where(
            slope_flag.fillna(False).astype(bool),
            np.maximum(risk, cfg["slope_reject_risk_floor"]),
            risk,
        )
    risk = np.clip(risk, 0, 100).round(1)

    def tier(score: float) -> str:
        if score < cfg["risk_safe_max"]:
            return "Space-Safe"
        if score < cfg["risk_borderline_max"]:
            return "Borderline"
        return "High-Risk"

    merged["risk_score"] = risk
    merged["reliability_index"] = (100 - risk).round(1)
    merged["reliability_tier"] = merged["risk_score"].apply(tier)
    # Confidence: high uncertainty ⇒ low confidence in the risk call. (Derived
    # here for the API — the notebook does not emit a confidence column.)
    merged["confidence"] = (
        1 - merged["Uncertainty_Score"].fillna(0.0) / 100.0
    ).clip(0.05, 0.99).round(3)
    return merged


def _risk_level_from_tier(tier: str) -> str:
    return {"Space-Safe": "LOW", "Borderline": "MEDIUM", "High-Risk": "HIGH"}.get(tier, "UNKNOWN")


def _is_flagged(v) -> bool:
    """Truthiness that survives NaN — `bool(float('nan'))` is True and would mis-explain."""
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "t"}
    return bool(v)


def _num(v, default: float = 0.0) -> float:
    """NaN-safe float coercion."""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(out) else out


# ---------------------------------------------------------------------------
# Explainability — portable SHAP bundle + drift SHAP
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_shap_bundle() -> dict:
    """Load the portable Module A SHAP bundle (plain dict, no custom classes)."""
    path = _artifact_path(SHAP_BUNDLE_ARTIFACT)
    if not path.exists():
        raise RuntimeError(f"SHAP bundle not found: {path}")
    import joblib
    try:
        return joblib.load(str(path))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load SHAP bundle: {exc}") from exc


def _impute_for_shap(frame: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    """
    Replicates Module A's LotMedianImputer.transform from the bundle's exported
    plain dicts — works regardless of which process fit the original pipeline.
    """
    frame = frame.copy()
    lot_col = bundle["lot_col"]
    for col in bundle["early_value_cols"]:
        if col not in frame.columns or frame[col].isna().sum() == 0:
            continue
        group_medians = bundle["group_medians"][col]
        fallback = frame[lot_col].map(group_medians).fillna(bundle["global_medians"][col])
        frame[col] = frame[col].fillna(fallback)
    return frame


def _add_drift_features_for_shap(frame: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    """Replicates Module A's add_drift_features() from the bundle's params."""
    frame = frame.copy()
    first_tp = bundle["early_timepoints"][0]
    last_tp = bundle["early_timepoints"][-1]
    for p in bundle["params"]:
        v_first = frame[f"{p}_{first_tp}"]
        v_last = frame[f"{p}_{last_tp}"]
        frame[f"{p}_delta_{last_tp}"] = v_last - v_first
        frame[f"{p}_pct_change_{last_tp}"] = np.where(
            v_first != 0, (v_last - v_first) / v_first.abs(), 0.0
        )
    return frame


def anomaly_shap_top_features(df: pd.DataFrame, top_k: int = 2) -> Dict[str, str]:
    """
    Standalone equivalent of MultivariateAnomalyScorer.shap_top_features(), driven
    by the portable bundle instead of a loaded class instance.

    Returns {Component_ID: "feat(+0.12), feat2(-0.03)"}. Requires `shap`.
    """
    import shap  # noqa: PLC0415 — optional dependency

    bundle = load_shap_bundle()
    GLOBAL_KEY = "__global__"
    feature_cols = list(bundle["feature_cols"])
    group_col = bundle["group_col"]

    needed = list(bundle["early_value_cols"]) + [bundle["lot_col"], group_col, "Component_ID"]
    frame = df[[c for c in needed if c in df.columns]].copy()
    frame = _impute_for_shap(frame, bundle)
    frame = _add_drift_features_for_shap(frame, bundle)
    frame = frame.set_index("Component_ID")

    models = bundle["iforest_models"]
    backgrounds = bundle["background"]

    out: Dict[str, str] = {}
    for group_value, group_df in frame.groupby(group_col):
        key = str(group_value) if str(group_value) in models else GLOBAL_KEY
        model = models[key]
        background = backgrounds[key]
        score_fn = lambda Xg, _m=model: -_m.decision_function(Xg)  # noqa: E731
        explainer = shap.Explainer(score_fn, background[feature_cols].values,
                                   feature_names=feature_cols)
        sv = explainer(group_df[feature_cols].values)
        for i, idx in enumerate(group_df.index):
            vals = np.asarray(sv.values[i]).reshape(-1)
            order = np.argsort(-np.abs(vals))[:top_k]
            out[str(idx)] = ", ".join(
                f"{feature_cols[j]}({vals[j]:+.2f})" for j in order
            )
    return out


def drift_shap_top_features(df: pd.DataFrame, top_k: int = 2,
                            dominant_metric: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    TreeExplainer attributions on the drift stack: for each component, the
    features driving the 168h forecast of its dominant predicted metric
    (mirrors the reliability notebook's drift SHAP pass).

    `dominant_metric` maps Component_ID → metric ("Leakage"/"Resistance"/"Vth");
    when omitted, the metric with the largest predicted drift is used.
    Returns {Component_ID: "feat(+0.303), feat2(+0.051)"}. Requires `shap`.
    """
    import shap  # noqa: PLC0415 — optional dependency

    bundle = load_drift_bundle()
    X, feats = _drift_feature_matrix(df, bundle)
    feature_names = list(X.columns)

    # index into the reconstructed targets:
    n_outputs = getattr(bundle["rf"], "n_outputs_", 2)
    if n_outputs == 2:
        target_idx = {"Leakage": 1}
    else:
        target_idx = {"Leakage": 1, "Resistance": 3, "Vth": 5}

    ids = list(feats["Component_ID"].astype(str))
    if dominant_metric is None:
        dominant_metric = {cid: "Leakage" for cid in ids}

    # Batched per metric — one explainer call per unique target.
    explainer = shap.TreeExplainer(bundle["rf"])
    out: Dict[str, str] = {}
    by_metric: Dict[str, List[int]] = {}
    for pos, cid in enumerate(ids):
        by_metric.setdefault(dominant_metric.get(cid, "Leakage"), []).append(pos)

    for metric, positions in by_metric.items():
        if metric not in target_idx:
            continue
        sv = explainer(X.iloc[positions])
        vals_all = np.asarray(sv.values)[:, :, target_idx[metric]]
        for i, pos in enumerate(positions):
            vals = vals_all[i]
            order = np.argsort(-np.abs(vals))[:top_k]
            out[ids[pos]] = ", ".join(
                f"{feature_names[j]}({vals[j]:+.3f})" for j in order
            )
    return out


def parse_shap_features(text: Optional[str]) -> List[Tuple[str, float]]:
    """Parse "Feature(+0.12), Other(-0.03)" into [("Feature", 0.12), ...]."""
    if not text:
        return []
    return [(name, float(value)) for name, value in _SHAP_FEATURE_RE.findall(str(text))]


# ---------------------------------------------------------------------------
class ExplanationTuple(tuple):
    """
    Richer explanation tuple preserving backward compatibility with 3-tuple unpacking
    (feature, contribution, reason) while carrying module and evidence_type metadata
    both as attributes (.module, .evidence_type) and as extended indices ([3], [4]).
    """
    def __new__(cls, feature, contribution, reason, module=None, evidence_type=None):
        return super().__new__(cls, (feature, contribution, reason))

    def __init__(self, feature, contribution, reason, module=None, evidence_type=None):
        self.feature = feature
        self.contribution = contribution
        self.reason = reason
        self.module = module
        self.evidence_type = evidence_type

    def __getitem__(self, index):
        if index == 3:
            return self.module
        elif index == 4:
            return self.evidence_type
        return super().__getitem__(index)


def build_explanations(
    row: pd.Series,
    module_b_status: Optional[str] = None,
) -> List[ExplanationTuple]:
    """
    Build explanation rows (feature, contribution, reason, module, evidence_type) for one component,
    using the same physics framing as the reliability notebook plus its SHAP
    attributions when they were computed.
    """
    out: List[ExplanationTuple] = []
    stress = str(row.get("Proxy_Stress", "")).strip()
    expected_param = STRESS_PHYSICS.get(stress)

    # Per-parameter early drift (0h → 24h), the strongest explainer.
    for m in PARAMETERS:
        v0 = row.get(f"{m}_0h")
        v24 = row.get(f"{m}_24h")
        if pd.isna(v0) or pd.isna(v24) or v0 == 0:
            continue
        pct = float((v24 - v0) / abs(v0) * 100.0)
        note = ""
        if m == expected_param:
            note = " (expected stress response)"
        elif abs(pct) > 5:
            note = " — atypical for this stress, worth a second look"
        out.append(ExplanationTuple(
            m, round(pct, 1),
            f"{m} moved {pct:+.1f}% in the first 24h under {stress}{note}.",
            "MODULE_A", "DRIFT",
        ))

    # SHAP attributions from the anomaly module (Module A's portable bundle).
    for feature, contribution in parse_shap_features(row.get("SHAP_Top_Anomaly_Features")):
        out.append(ExplanationTuple(
            feature, contribution,
            f"Anomaly score influenced by {feature} ({contribution:+.2f} SHAP attribution).",
            "MODULE_A", "SHAP",
        ))

    # SHAP attributions from the drift module (Module B's stack).
    for feature, contribution in parse_shap_features(row.get("SHAP_Top_Drift_Features")):
        metric = str(row.get("Dominant_Drift_Metric") or "").strip()
        where = f"predicted {metric} 168h drift" if metric else "168h drift forecast"
        out.append(ExplanationTuple(
            feature, contribution,
            f"{where} influenced by {feature} ({contribution:+.3f} SHAP attribution).",
            "MODULE_B", "SHAP",
        ))

    # Lot-relative outlier signal from Module A.
    z = row.get("Worst_Lot_Zscore")
    if z is not None and not pd.isna(z):
        out.append(ExplanationTuple(
            "Lot_Relative", round(float(z), 2),
            f"Batch-relative outlier (z={float(z):.2f} vs its own lot).",
            "MODULE_A", "DRIFT",
        ))

    # Absolute spec check.
    if int(row.get("Absolute_Spec_Fail", 0) or 0) == 1:
        out.append(ExplanationTuple(
            "Traditional_Test_Result", 1.0,
            "Failed traditional fixed-limit spec check.",
            "MODULE_C", "RULE",
        ))

    # Safety-slope early rejection (new in the v5.1 stack).
    if _is_flagged(row.get("Slope_Reject_Flag")):
        out.append(ExplanationTuple(
            "Slope_Reject_Flag", 1.0,
            "Predicted 168h drift rate exceeds the Safe-population safety slope — "
            "flagged for early rejection.",
            "MODULE_B", "RULE",
        ))

    # Forecast signal from Module B.
    b_status = (module_b_status or str(row.get("module_b_status") or row.get("Module_B_Status") or "")).strip().lower()
    if b_status == "degraded":
        out.append(ExplanationTuple(
            "Predicted_Drift", None,
            "Module B drift estimation is degraded; a reliable 168h drift forecast is unavailable.",
            "MODULE_B", "FALLBACK",
        ))
    else:
        pds = row.get("Predicted_Drift_Score")
        if pds is not None and not pd.isna(pds):
            out.append(ExplanationTuple(
                "Predicted_Drift", round(float(pds), 1),
                "Forecast to 168h shows continued significant drift.",
                "MODULE_B", "DRIFT",
            ))

        us = row.get("Uncertainty_Score")
        if us is not None and not pd.isna(us):
            out.append(ExplanationTuple(
                "Uncertainty", round(float(us), 1),
                "Forecast confidence is low (wide interval and/or RF/GB disagreement) — "
                "recommend extended monitoring rather than trusting the point estimate.",
                "MODULE_B", "DRIFT",
            ))

    if not out:
        out.append(ExplanationTuple("Summary", None, "No significant deviation detected.", "MODULE_C", "RULE"))
    return out


# ---------------------------------------------------------------------------
# Fallback scoring (no artifacts / no sklearn)
# ---------------------------------------------------------------------------

def fallback_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pure-statistical fallback used when the artifacts can't be loaded:
    lot-relative robust z-scores, early-drift severity, and a linear
    slope extrapolation to 168h (the notebook's own baseline predictor).
    """
    rows = []
    for _, grp in df.groupby("Batch_ID"):
        for _, row in grp.iterrows():
            worst_z, max_drift = 0.0, 0.0
            for m in PARAMETERS:
                vals = pd.to_numeric(grp[f"{m}_24h"], errors="coerce")
                med = vals.median()
                mad = (vals - med).abs().median() * 1.4826
                mad = mad if mad > 0 else 1e-9
                z = abs((row[f"{m}_24h"] - med) / mad) if not pd.isna(row[f"{m}_24h"]) else 0.0
                worst_z = max(worst_z, float(z))
                v0, v24 = row.get(f"{m}_0h"), row.get(f"{m}_24h")
                if not pd.isna(v0) and not pd.isna(v24) and v0 != 0:
                    max_drift = max(max_drift, abs(float((v24 - v0) / v0)))

            lot_score = min(worst_z / 8.0 * 100, 100.0)
            drift_score = min(max_drift * 100, 100.0)
            elapsed_24h = float(row.get("elapsed_h_24h") or 24.0) or 24.0

            out_row = {
                "Component_ID": row["Component_ID"],
                "Batch_ID": row["Batch_ID"],
                "Part_Type": row["Part_Type"],
                "Absolute_Spec_Fail": int(
                    str(row.get("Traditional_Test_Result", "Pass")).strip().lower() == "fail"
                ),
                "Lot_Relative_Score": round(lot_score, 1),
                "Worst_Lot_Zscore": round(worst_z, 2),
                "Multivariate_Score": 0.0,
                "Drift_Score": round(drift_score, 1),
                "Anomaly_Risk_Score": round(lot_score * 0.5 + drift_score * 0.5, 1),
                "QA_Decision": "PASS",
                "Predicted_Drift_Score": round(drift_score, 1),
                "Uncertainty_Score": 0.0,
                "Slope_Reject_Flag": False,
                "model_version": FALLBACK_MODEL_VERSION,
            }
            for m in PARAMETERS:
                v0, v24 = row.get(f"{m}_0h"), row.get(f"{m}_24h")
                if pd.isna(v0) or pd.isna(v24):
                    continue
                slope = float(v24 - v0) / elapsed_24h
                out_row[f"Pred_{m}_168h"] = float(v24) + slope * (DRIFT_HORIZON_HOURS - elapsed_24h)
                out_row[f"Predicted_Drift_{m}"] = (
                    abs(out_row[f"Pred_{m}_168h"] - float(v24)) / abs(float(v24)) if v24 else 0.0
                )
            rows.append(out_row)

    out = pd.DataFrame(rows)
    if not out.empty:
        out["decision"] = out["QA_Decision"]
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _attach_explainability(merged: pd.DataFrame, df: pd.DataFrame, warnings: List[str]) -> None:
    """
    Compute SHAP attributions for the flagged components (Borderline/High-Risk),
    capped by the configured row budget. Best-effort: a missing `shap`
    dependency or a slow run never fails ingestion.
    """
    cfg = explainability_config()
    if not cfg["enabled"]:
        return

    try:
        import shap  # noqa: F401,PLC0415 — probe only
    except Exception:  # noqa: BLE001
        warnings.append("Explainability requested but `shap` is not installed; SHAP features skipped.")
        return

    flagged = merged["reliability_tier"].isin(["Borderline", "High-Risk"])
    ids = [str(c) for c in merged.loc[flagged, "Component_ID"]]
    budget = int(cfg["max_rows"])
    if budget > 0:
        ids = ids[:budget]
    if not ids:
        return

    subset = df[df["Component_ID"].astype(str).isin(ids)]
    top_k = int(cfg["top_k"])

    try:
        anomaly_text = anomaly_shap_top_features(subset, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Anomaly SHAP unavailable ({exc}); skipped.")
        anomaly_text = {}

    dominant = {
        str(row["Component_ID"]): max(
            [m for m in PARAMETERS if f"Predicted_Drift_{m}" in row] or ["Leakage"],
            key=lambda m: _num(row.get(f"Predicted_Drift_{m}")),
            default="Leakage",
        )
        for _, row in merged[merged["Component_ID"].astype(str).isin(ids)].iterrows()
    }
    try:
        drift_text = drift_shap_top_features(subset, top_k=top_k, dominant_metric=dominant)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Drift SHAP unavailable ({exc}); skipped.")
        drift_text = {}

    merged["SHAP_Top_Anomaly_Features"] = merged["Component_ID"].astype(str).map(anomaly_text)
    merged["SHAP_Top_Drift_Features"] = merged["Component_ID"].astype(str).map(drift_text)
    merged["Dominant_Drift_Metric"] = merged["Component_ID"].astype(str).map(dominant)

    explained = int(merged["SHAP_Top_Anomaly_Features"].notna().sum())
    explained += int(merged["SHAP_Top_Drift_Features"].notna().sum())
    if explained:
        warnings.append(
            f"SHAP explanations computed for {len(ids)} flagged component(s) "
            f"(budget {budget if budget > 0 else 'unlimited'} rows)."
        )


def run_inference(df: pd.DataFrame) -> Tuple[pd.DataFrame, str, List[str]]:
    """
    Run the full 3-module stack over the cleaned wide frame.
    Returns (scores_df, model_version_used, warnings).

    scores_df contains Module A columns + Module C risk/decision + Module B
    drift columns, one row per component, plus `split` when the input carries it.
    """
    warnings: List[str] = []
    df = df.copy()
    anomaly_ok = True
    module_a_status = "loaded"
    module_b_status = "loaded"
    module_c_status = "active"

    # Module A — anomaly risk + decision
    try:
        pipeline = load_anomaly_pipeline()
        anomaly_df = pipeline.predict(df)
        anomaly_df["model_version"] = MODEL_VERSION
        module_a_status = "loaded"
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Module A unavailable ({exc}); using statistical fallback.")
        anomaly_df = fallback_scores(df)
        anomaly_ok = False
        module_a_status = "fallback"

    # Module B — 168h forecast
    try:
        drift, drift_warnings = run_module_b(df)
        warnings.extend(drift_warnings)
        module_b_status = "loaded"
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Module B unavailable ({exc}); skipping drift forecast.")
        drift = pd.DataFrame({
            "Component_ID": df["Component_ID"].values,
            "Predicted_Drift_Score": 0.0,
            "Uncertainty_Score": 0.0,
            "Slope_Reject_Flag": False,
        })
        module_b_status = "degraded"

    merged = compute_reliability(anomaly_df, drift)
    module_c_status = "active"
    merged["risk_level"] = merged["reliability_tier"].map(_risk_level_from_tier)
    # Map QA decision labels to the API vocabulary (already PASS/MONITOR/HOLD/REJECT).
    merged["decision"] = merged["QA_Decision"].astype(str).str.strip()
    merged["confidence"] = merged["confidence"].fillna(0.5)

    _attach_explainability(merged, df, warnings)

    # Attach Phase 17 runtime module statuses safely to DataFrame attrs
    merged.attrs["trace_info"] = {
        "module_a_status": module_a_status,
        "module_b_status": module_b_status,
        "module_c_status": module_c_status,
    }

    # The stored stamp reflects Module A, which owns the decision. A partial
    # degradation (Module B only) is reported through `warnings` instead.
    version = MODEL_VERSION if anomaly_ok else FALLBACK_MODEL_VERSION
    return merged, version, warnings
