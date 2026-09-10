"""
Compatibility shim for `models/pipeline.joblib` (Module A).

The anomaly-detection pipeline was trained inside `notebooks/anomaly_detection.ipynb`
and pickled from the notebook's `__main__` namespace. Python pickle stores classes
*by reference*, so unpickling requires classes with the same names to be importable
under `__main__` at load time.

This module reproduces those class definitions verbatim from the notebook (same
names, same field names, same math) and `ml_inference.register_notebook_classes()`
publishes them on `__main__` just before `joblib.load()`.

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

    params: List[str] = field(default_factory=lambda: ["Leakage", "Resistance", "Vth"])
    early_timepoints: List[str] = field(default_factory=lambda: ["0h", "24h"])

    weight_lot_relative: float = 0.40
    weight_multivariate: float = 0.40
    weight_drift: float = 0.20

    threshold_monitor: float = 30.0
    threshold_hold: float = 60.0
    threshold_reject: float = 80.0

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
        return [self.id_col, self.lot_col, self.group_col, self.absolute_result_col] + self.early_value_cols()

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
        self._fitted = False

    def fit(self, df: pd.DataFrame) -> "LotMedianImputer":
        lot_col = self.config.lot_col
        for col in self.columns:
            self.group_medians_[col] = df.groupby(lot_col)[col].median().to_dict()
            self.global_medians_[col] = float(df[col].median())
        self._fitted = True
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("LotMedianImputer must be fit before transform")
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
    def __init__(self, config: PipelineConfig):
        self.config = config

    def transform(self, df: pd.DataFrame) -> pd.Series:
        col, fail_value = self.config.absolute_result_col, self.config.absolute_fail_value
        return (df[col].astype(str).str.strip().str.lower() == fail_value.lower()).astype(int)


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
        self._fitted = False

    def fit(self, df: pd.DataFrame) -> "LotRelativeScorer":
        lot_col = self.config.lot_col
        for col in self.columns:
            self.group_stats_[col] = df.groupby(lot_col)[col].apply(_robust_median_mad).to_dict()
            self.global_stats_[col] = _robust_median_mad(df[col])
        self._fitted = True
        return self

    def transform(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        if not self._fitted:
            raise RuntimeError("LotRelativeScorer must be fit before transform")
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
        self._fitted = False

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

    def transform(self, df: pd.DataFrame) -> pd.Series:
        if not self._fitted:
            raise RuntimeError("MultivariateAnomalyScorer must be fit before transform")
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


class DriftSeverityScorer:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.bound_: Optional[float] = None

    def _severity(self, df: pd.DataFrame) -> pd.Series:
        cols = [self.config.pct_change_col(p) for p in self.config.params]
        return df[cols].abs().max(axis=1)

    def transform(self, df: pd.DataFrame) -> pd.Series:
        if self.bound_ is None:
            raise RuntimeError("DriftSeverityScorer must be fit before transform")
        return (self._severity(df).clip(upper=self.bound_) / self.bound_) * 100


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
        self._fitted = False
        self._n_training_rows = None
        self._fitted_at = None

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("Pipeline must be fit (or loaded) before predict()")
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
