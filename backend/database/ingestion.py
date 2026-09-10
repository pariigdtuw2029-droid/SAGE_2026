"""
End-to-end ingestion pipeline (Member 2).

    CSV → parse → validate → preprocess → store base tables
        → run 3-module inference → store predictions / risk_assessments / explanations
        → refresh component snapshots + lot counts

`ingest_csv_file` is the single entry point; `ingest_dataframe` can be reused
by Member 1's upload endpoint (bytes → pandas) without going through disk.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd
from sqlalchemy.orm import Session

from database import crud, models, ml_inference, preprocessing, validation
from database.validation import DataValidationError


@dataclass
class IngestionResult:
    filename: Optional[str] = None
    total_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    lots_created: int = 0
    components_created: int = 0
    measurements_created: int = 0
    predictions_created: int = 0
    risk_assessments_created: int = 0
    explanations_created: int = 0
    model_version: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    issues: List[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.components_created > 0 or self.valid_rows > 0

    def summary(self) -> dict:
        return {
            "filename": self.filename,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "rejected_rows": self.rejected_rows,
            "lots_created": self.lots_created,
            "components_created": self.components_created,
            "measurements_created": self.measurements_created,
            "predictions_created": self.predictions_created,
            "risk_assessments_created": self.risk_assessments_created,
            "explanations_created": self.explanations_created,
            "model_version": self.model_version,
            "warnings": self.warnings,
            "issues": self.issues[:50],
        }


def ingest_dataframe(df: pd.DataFrame, db: Optional[Session] = None,
                     filename: Optional[str] = None) -> IngestionResult:
    """
    Validate → preprocess → store → infer → store model outputs.
    Raises DataValidationError when the dataset itself is unusable (wrong columns).
    """
    result = IngestionResult(filename=filename, total_rows=len(df))

    # 1. Validation
    report = validation.validate_dataframe(df)
    result.rejected_rows = report.rejected_rows
    result.valid_rows = report.valid_rows
    result.issues = [
        {"row": i.row_index, "component_id": i.component_id, "column": i.column,
         "type": i.issue_type, "detail": i.detail}
        for i in report.issues
    ]
    if not report.is_valid:
        raise DataValidationError(report)

    # 2. Preprocessing (cleaning, units, imputation, timestamps, relationships)
    valid_df = validation.filter_valid_rows(df, report)
    cleaned = preprocessing.preprocess(valid_df)
    result.warnings.extend(cleaned.issues)

    # 3. Base tables: lots + components + measurements
    with crud.session_scope() if db is None else _nullcontext(db) as s:
        for _, row in cleaned.lots.iterrows():
            crud.create_lot(row["lot_id"], component_type=row["component_type"],
                            temperature=row["temperature"],
                            total_components=int(row["total_components"]), db=s,
                            stress_type=row.get("stress_type"), stress_level=row.get("stress_level"),
                            stress_unit=row.get("stress_unit"), part_family=row.get("part_family"))
            result.lots_created += 1

        for _, row in cleaned.components.iterrows():
            crud.create_component(row["component_id"], lot_id=row["lot_id"],
                                  component_type=row["component_type"], status=row["status"], db=s,
                                  traditional_result=row.get("traditional_result"),
                                  label=row.get("label"))
            result.components_created += 1

        result.measurements_created = crud.add_measurements(
            cleaned.measurements.to_dict("records"), db=s
        )
        # Flush so later `s.get(...)` lookups (snapshot refresh) and the
        # idempotency deletes see rows created in this transaction.
        s.flush()

        # 4. Inference (Modules A + B + C)
        scores, model_version, ml_warnings = ml_inference.run_inference(cleaned.df)
        result.model_version = model_version
        result.warnings.extend(ml_warnings)

        # 4b. Idempotency: clear previous model outputs for these components so
        # re-ingesting a lot never duplicates predictions/risk/explanations.
        component_ids = [str(c) for c in cleaned.components["component_id"]]
        if component_ids:
            s.query(models.Prediction).filter(
                models.Prediction.component_id.in_(component_ids)).delete(synchronize_session=False)
            s.query(models.RiskAssessment).filter(
                models.RiskAssessment.component_id.in_(component_ids)).delete(synchronize_session=False)
            s.query(models.Explanation).filter(
                models.Explanation.component_id.in_(component_ids)).delete(synchronize_session=False)

        # 5. Store predictions (per parameter), risk assessments, explanations
        for _, row in scores.iterrows():
            cid = str(row["Component_ID"])
            for m in ["Leakage", "Resistance", "Vth"]:
                pred_col = f"Pred_{m}_168h"
                if pred_col not in row or pd.isna(row[pred_col]):
                    continue
                crud.add_prediction(
                    cid, parameter=m, predicted_168h=float(row[pred_col]), db=s,
                    actual_168h=_actual_168h(cleaned.df, cid, m),
                    interval_low=_safe_float(row.get(f"Interval_Width_{m}")),
                    interval_high=None,
                    uncertainty_score=_safe_float(row.get("Uncertainty_Score")),
                    model_version=model_version,
                )
                result.predictions_created += 1

            crud.add_risk_assessment(
                cid,
                anomaly_score=float(row["Anomaly_Risk_Score"]),
                drift_score=float(row["Drift_Score"]),
                risk_score=float(row["risk_score"]),
                decision=str(row["decision"]),
                confidence=float(row["confidence"]), db=s,
                predicted_drift_score=_safe_float(row.get("Predicted_Drift_Score")),
                reliability_index=_safe_float(row.get("reliability_index")),
                lot_relative_score=_safe_float(row.get("Lot_Relative_Score")),
                multivariate_score=_safe_float(row.get("Multivariate_Score")),
                worst_lot_zscore=_safe_float(row.get("Worst_Lot_Zscore")),
                absolute_spec_fail=int(row.get("Absolute_Spec_Fail", 0) or 0),
                model_version=model_version,
            )
            result.risk_assessments_created += 1

            explanations = ml_inference.build_explanations(row)
            result.explanations_created += crud.add_explanations(cid, explanations, db=s)

        # 6. Refresh component snapshots + lot counts
        for _, row in scores.iterrows():
            cid = str(row["Component_ID"])
            crud.update_component_snapshot(
                cid, db=s,
                status=str(row["decision"]),
                risk_level=str(row["risk_level"]),
                anomaly_score=float(row["Anomaly_Risk_Score"]),
                drift_score=float(row["Drift_Score"]),
                risk_score=float(row["risk_score"]),
                decision=str(row["decision"]),
                confidence=float(row["confidence"]),
                reliability_index=_safe_float(row.get("reliability_index")),
            )
        for lot_id in cleaned.lots["lot_id"]:
            crud.update_lot_counts(lot_id, db=s)

    return result


def ingest_csv_file(path: Union[str, Path], db: Optional[Session] = None,
                    limit: Optional[int] = None) -> IngestionResult:
    """Read a CSV file from disk and ingest it."""
    path = Path(path)
    df = pd.read_csv(path)
    if limit is not None:
        df = df.head(limit)
    return ingest_dataframe(df, db=db, filename=path.name)


def _actual_168h(clean_df: pd.DataFrame, component_id: str, parameter: str) -> Optional[float]:
    """Look up the measured 168h value for a component/parameter in the wide frame."""
    row = clean_df.loc[clean_df["Component_ID"] == component_id]
    if row.empty:
        return None
    col = f"{parameter}_168h"
    if col not in row.columns:
        return None
    v = pd.to_numeric(row[col], errors="coerce").iloc[0]
    return None if pd.isna(v) else float(v)


def _safe_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc):
        return False