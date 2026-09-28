"""
Compatibility shim for `models/anomaly_pipeline.joblib` (Module A).

The anomaly-detection pipeline was trained inside `notebooks/anomaly_detection.ipynb`
and pickled from the notebook's `__main__` namespace. Python pickle stores classes
*by reference*, so unpickling requires classes with the same names to be importable
under `__main__` at load time.

This module reproduces those class definitions verbatim from the notebook (same
names, same attributes, same math) and `ml_inference._register_notebook_classes()`
publishes them on `__main__` just before `joblib.load()`.

Mirrored against the current notebook revision (batch-grouped train/val/test split,
per-group SHAP backgrounds, tuned thresholds inside the fitted `PipelineConfig`):

  • ``AnomalyPipeline`` now exposes ``fit()`` and carries **no** ``_fitted`` flag —
    unpickling bypasses ``__init__``, so a stale guard here would raise
    ``AttributeError`` on every load. Keep this file in lock-step with the notebook.
  • ``MultivariateAnomalyScorer`` gained ``background_`` (per-group sample kept for
    SHAP) and ``shap_top_features()``, alongside ``fit()``.

Nothing here trains anything — the fitted state comes from the artifact. If the
notebook is retrained, re-dump it and keep this file in sync.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
# Config (mirrors notebook "PipelineConfig")
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    id_col: str = "Component_ID"
    lot_col: str = "Batch_ID"
    group_col: str = "Part_Type"
    absolute_result_col: str = "Traditional_Test_Result"
    absolute_fail_value: str = "Fail"
    label_col: str = "Label"

    params: List[str] = field(default_factory=lambda: ["Leakage"])
    early_timepoints: List[str] = field(default_factory=lambda: ["0h", "24h"])

    weight_lot_relative: float = 0.095
    weight_multivariate: float = 0.235
    weight_drift: float = 0.670

    threshold_monitor: float = 15.5
    threshold_hold: float = 15.9
    threshold_reject: float = 96.8

    z_score_cap: float = 8.0

    iforest_n_estimators: int = 300
    iforest_contamination: str = "auto"
    iforest_min_group_size: int = 30
    random_state: int = 42

    def __post_init__(self):
        total = self.weight_lot_relative + self.weight_multivariate + self.weight_drift
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Layer weights must sum to 1.0, got {total:.3f}")
        if not (self.threshold_monitor < self.threshold_hold < self.threshold_reject):
            raise ValueError("Thresholds must be strictly increasing")

    def value_col(self, param, timepoint):
        return f"{param}_{timepoint}"

    def delta_col(self, param):
        return f"{param}_delta_24h"

    def pct_change_col(self, param):
        return f"{param}_pct_change_24h"

    def early_value_cols(self):
        return [self.value_col(p, t) for p in self.params for t in self.early_timepoints]

    def lot_relative_cols(self):
        latest = self.early_timepoints[-1]
        return [self.value_col(p, latest) for p in self.params] + [self.delta_col(p) for p in self.params]

    def multivariate_feature_cols(self):
        cols = []
        for p in self.params:
            cols += [self.value_col(p, t) for t in self.early_timepoints]
            cols += [self.delta_col(p), self.pct_change_col(p)]
        return cols

    def required_input_cols(self):
        return [self.id_col, self.lot_col, self.group_col] + self.early_value_cols()

    def to_dict(self):
        return asdict(self)


class SchemaError(ValueError):
    pass


def validate_schema(df: pd.DataFrame, config: PipelineConfig, require_label: bool = False) -> None:
    required = list(config.required_input_cols())
    if require_label:
        required.append(config.label_col)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SchemaError(f"Input data is missing required columns: {missing}")
    n_dupes = df[config.id_col].duplicated().sum()
    if n_dupes:
        print(f"WARNING: {n_dupes} duplicate {config.id_col} values found")


class LotMedianImputer:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.columns = config.early_value_cols()
        self.group_medians_: Dict[str, dict] = {}
        self.global_medians_: Dict[str, float] = {}

    def fit(self, df: pd.DataFrame) -> "LotMedianImputer":
        lot_col = self.config.lot_col
        for col in self.columns:
            self.group_medians_[col] = df.groupby(lot_col)[col].median().to_dict()
            self.global_medians_[col] = float(df[col].median())
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        lot_col = self.config.lot_col
        for col in self.columns:
            if df[col].isna().sum() == 0:
                continue
            fallback = df[lot_col].map(self.group_medians_[col]).fillna(self.global_medians_[col])
            df[col] = df[col].fillna(fallback)
        return df

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.fit(df).transform(df)


def add_drift_features(df: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    df = df.copy()
    first_tp, last_tp = config.early_timepoints[0], config.early_timepoints[-1]
    for p in config.params:
        v_first = df[config.value_col(p, first_tp)]
        v_last = df[config.value_col(p, last_tp)]
        df[config.delta_col(p)] = v_last - v_first
        df[config.pct_change_col(p)] = np.where(v_first != 0, (v_last - v_first) / v_first.abs(), 0.0)
    return df


class AbsoluteSpecLayer:
    """Static datasheet-limit check.
    - When Traditional_Test_Result is present in the input (historical/backtest data),
      we use that ground truth directly.
    - When absent (genuinely new live component), we flag anything that exceeded
      the datasheet limit learned at fit time.
    """
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.limits_: Dict[str, float] = {}

    def fit(self, df: pd.DataFrame) -> "AbsoluteSpecLayer":
        for p in self.config.params:
            cols = [c for c in df.columns if c.startswith(f"{p}_") and c[len(p)+1:].endswith("h")]
            if cols:
                self.limits_[p] = float(df[cols].max().max())
        return self

    def transform(self, df: pd.DataFrame) -> pd.Series:
        col, fail_value = self.config.absolute_result_col, self.config.absolute_fail_value
        if col in df.columns:
            return (df[col].astype(str).str.strip().str.lower() == fail_value.lower()).astype(int)
        exceeded = pd.Series(False, index=df.index)
        for p in self.config.params:
            limit = self.limits_.get(p)
            if limit is None:
                continue
            for t in self.config.early_timepoints:
                c = self.config.value_col(p, t)
                if c in df.columns:
                    exceeded |= (df[c] > limit)
        return exceeded.astype(int)


def _robust_median_mad(series: pd.Series) -> Tuple[float, float]:
    median = float(series.median())
    mad = float((series - median).abs().median()) * 1.4826
    return median, mad


class LotRelativeScorer:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.columns = config.lot_relative_cols()
        self.group_stats_: Dict[str, Dict] = {}
        self.global_stats_: Dict[str, Tuple[float, float]] = {}

    def fit(self, df: pd.DataFrame) -> "LotRelativeScorer":
        lot_col = self.config.lot_col
        for col in self.columns:
            self.group_stats_[col] = df.groupby(lot_col)[col].apply(_robust_median_mad).to_dict()
            self.global_stats_[col] = _robust_median_mad(df[col])
        return self

    def transform(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        lot_col = self.config.lot_col
        abs_z_frame = pd.DataFrame(index=df.index)
        for col in self.columns:
            medians = df[lot_col].map(lambda lot: self.group_stats_[col].get(lot, (np.nan, np.nan))[0])
            mads = df[lot_col].map(lambda lot: self.group_stats_[col].get(lot, (np.nan, np.nan))[1])
            global_median, global_mad = self.global_stats_[col]
            medians = medians.fillna(global_median)
            mads = mads.fillna(global_mad).replace(0, global_mad if global_mad > 0 else 1e-6)
            abs_z_frame[col] = ((df[col] - medians) / mads).abs()
        worst_z = abs_z_frame.max(axis=1)
        score = (worst_z.clip(upper=self.config.z_score_cap) / self.config.z_score_cap) * 100
        return score, worst_z


GLOBAL_KEY = "__global__"


class MultivariateAnomalyScorer:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.feature_cols = config.multivariate_feature_cols()
        self.models_: Dict[str, Pipeline] = {}
        self.score_bounds_: Dict[str, Tuple[float, float]] = {}
        # Per-group background sample kept at fit time so SHAP can be computed
        # later without re-reading the training frame.
        self.background_: Dict[str, pd.DataFrame] = {}

    def _fit_one(self, X: np.ndarray):
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("iforest", IsolationForest(
                n_estimators=self.config.iforest_n_estimators,
                contamination=self.config.iforest_contamination,
                random_state=self.config.random_state,
            )),
        ])
        pipeline.fit(X)
        raw = -pipeline.decision_function(X)
        p1, p99 = np.percentile(raw, [1, 99])
        return pipeline, (float(p1), float(p99))

    def fit(self, df: pd.DataFrame) -> "MultivariateAnomalyScorer":
        group_col = self.config.group_col
        n_bg = min(100, len(df))
        model, bounds = self._fit_one(df[self.feature_cols].values)
        self.models_[GLOBAL_KEY] = model
        self.score_bounds_[GLOBAL_KEY] = bounds
        self.background_[GLOBAL_KEY] = df[self.feature_cols].sample(
            n_bg, random_state=self.config.random_state
        )
        for group_value, group_df in df.groupby(group_col):
            if len(group_df) < self.config.iforest_min_group_size:
                print(f"WARNING: group '{group_value}' has only {len(group_df)} rows; using global fallback")
                continue
            model, bounds = self._fit_one(group_df[self.feature_cols].values)
            self.models_[str(group_value)] = model
            self.score_bounds_[str(group_value)] = bounds
            self.background_[str(group_value)] = group_df[self.feature_cols].sample(
                min(100, len(group_df)), random_state=self.config.random_state
            )
        return self

    def transform(self, df: pd.DataFrame) -> pd.Series:
        group_col = self.config.group_col
        scores = pd.Series(index=df.index, dtype=float)
        for group_value, group_df in df.groupby(group_col):
            key = str(group_value) if str(group_value) in self.models_ else GLOBAL_KEY
            model = self.models_[key]
            p1, p99 = self.score_bounds_[key]
            X = group_df[self.feature_cols].values
            raw = -model.decision_function(X)
            norm = np.clip((raw - p1) / (p99 - p1 + 1e-9), 0, 1) * 100
            scores.loc[group_df.index] = norm
        return scores

    def shap_top_features(self, df: pd.DataFrame, top_k: int = 3) -> pd.Series:
        """
        Top-|SHAP| multivariate features per component for the rows in `df`.
        Requires the optional `shap` dependency (training/explainability only).
        """
        import shap  # noqa: PLC0415 — optional dependency

        group_col = self.config.group_col
        out = pd.Series(index=df.index, dtype=object)
        for group_value, group_df in df.groupby(group_col):
            key = str(group_value) if str(group_value) in self.models_ else GLOBAL_KEY
            model = self.models_[key]
            background = self.background_[key]
            score_fn = lambda X, _m=model: -_m.decision_function(X)  # noqa: E731
            explainer = shap.Explainer(score_fn, background.values, feature_names=self.feature_cols)
            sv = explainer(group_df[self.feature_cols].values)
            for i, idx in enumerate(group_df.index):
                vals = np.asarray(sv.values[i]).reshape(-1)
                order = np.argsort(-np.abs(vals))[:top_k]
                out.loc[idx] = ", ".join(
                    f"{self.feature_cols[j]}({vals[j]:+.2f})" for j in order
                )
        return out


class DriftSeverityScorer:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.bound_: Optional[float] = None

    def _severity(self, df: pd.DataFrame) -> pd.Series:
        cols = [self.config.pct_change_col(p) for p in self.config.params]
        return df[cols].abs().max(axis=1)

    def fit(self, df: pd.DataFrame) -> "DriftSeverityScorer":
        bound = float(self._severity(df).quantile(0.99))
        self.bound_ = bound if bound > 0 else 1e-6
        return self

    def transform(self, df: pd.DataFrame) -> pd.Series:
        bound = getattr(self, "bound_", None)
        if bound is None:
            raise RuntimeError("DriftSeverityScorer must be fit before transform")
        return (self._severity(df).clip(upper=bound) / bound) * 100


def decide(risk_score: pd.Series, config: PipelineConfig) -> pd.Series:
    bins = [-np.inf, config.threshold_monitor, config.threshold_hold, config.threshold_reject, np.inf]
    labels = ["PASS", "MONITOR", "HOLD", "REJECT"]
    return pd.cut(risk_score, bins=bins, labels=labels, right=False).astype(str)


def combine(lot_score, multivariate_score, drift_score, absolute_fail, config: PipelineConfig) -> pd.Series:
    combined = (
        config.weight_lot_relative * lot_score
        + config.weight_multivariate * multivariate_score
        + config.weight_drift * drift_score
    )
    combined = np.where(absolute_fail == 1, np.maximum(combined, config.threshold_reject + 5), combined)
    combined = np.clip(combined, 0, 100)
    return pd.Series(np.round(combined, 1), index=lot_score.index)


class AnomalyPipeline:
    """Structure-only mirror of the notebook class; fitted state arrives via pickle."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.imputer = LotMedianImputer(self.config)
        self.lot_scorer = LotRelativeScorer(self.config)
        self.mv_scorer = MultivariateAnomalyScorer(self.config)
        self.drift_scorer = DriftSeverityScorer(self.config)
        self.absolute_layer = AbsoluteSpecLayer(self.config)

    def fit(self, df: pd.DataFrame) -> "AnomalyPipeline":
        """Fit every layer on the training split (mirrors the notebook)."""
        validate_schema(df, self.config)
        df = self.imputer.fit_transform(df)
        df = add_drift_features(df, self.config)
        self.lot_scorer.fit(df)
        self.mv_scorer.fit(df)
        self.drift_scorer.fit(df)
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        # NOTE: no fitted-state guard here on purpose — the notebook class has none,
        # and unpickling bypasses __init__ so any flag would be absent on load.
        validate_schema(df, self.config)
        original_index = df.index

        df = self.imputer.transform(df)
        df = add_drift_features(df, self.config)

        absolute_fail = self.absolute_layer.transform(df)
        lot_score, worst_z = self.lot_scorer.transform(df)
        mv_score = self.mv_scorer.transform(df)
        drift_score = self.drift_scorer.transform(df)
        risk_score = combine(lot_score, mv_score, drift_score, absolute_fail, self.config)
        decision = decide(risk_score, self.config)

        result = pd.DataFrame({
            self.config.id_col: df[self.config.id_col],
            self.config.lot_col: df[self.config.lot_col],
            self.config.group_col: df[self.config.group_col],
            "Absolute_Spec_Fail": absolute_fail,
            "Lot_Relative_Score": lot_score.round(1),
            "Worst_Lot_Zscore": worst_z.round(2),
            "Multivariate_Score": mv_score.round(1),
            "Drift_Score": drift_score.round(1),
            "Anomaly_Risk_Score": risk_score,
            "QA_Decision": decision,
        }, index=original_index)

        if self.config.label_col in df.columns:
            result[self.config.label_col] = df[self.config.label_col]
        if self.config.absolute_result_col in df.columns:
            result[self.config.absolute_result_col] = df[self.config.absolute_result_col]
        return result
