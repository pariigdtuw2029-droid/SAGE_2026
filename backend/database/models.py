"""
ORM models (Member 2) — the six-table structure from the project spec.

Database
├── lots
├── components
├── measurements
├── predictions
├── risk_assessments
└── explanations

Mapping from the SAGE burn-in domain to the spec's tables:
- ``lots``            : one row per Batch_ID; component_type, stress level/unit,
                        temperature (Proxy_Stress "Thermal-High" → bake °C level).
- ``components``      : one row per Component_ID; ``status`` = traditional result,
                        ``component_type`` = Part_Type; risk_level mirrors tier.
                        ``split`` is the batch-grouped train/val/test role the
                        anomaly notebook assigned the component's lot.
- ``measurements``    : long format — one row per (component, timestamp, parameter)
                        for Leakage (µA), Resistance (Ω), Vth (V).
- ``predictions``     : Module B drift-forecast output (predicted_168h / actual_168h
                        / prediction_error per parameter). interval_low/interval_high
                        are the quantile-0.05/0.95 forecast bounds in physical units
                        (the *width* stays available via interval_high - interval_low).
- ``risk_assessments``: fused risk from Modules A + B (anomaly, drift, risk scores,
                        decision, confidence) plus the v5.1 safety-slope flag, the
                        reliability tier, and the SHAP top features when computed.
- ``explanations``    : per-feature contribution rows backing the report reasons.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class Lot(Base):
    __tablename__ = "lots"

    lot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    component_type: Mapped[str] = mapped_column(String(64))
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_components: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="IN_REVIEW")
    stress_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stress_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    part_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    components: Mapped[list["Component"]] = relationship(
        back_populates="lot", cascade="all, delete-orphan"
    )


class Component(Base):
    __tablename__ = "components"

    component_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.lot_id", ondelete="CASCADE"), index=True)
    component_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")

    # Latest screening snapshot (kept here for cheap list views; source of truth
    # lives in risk_assessments / predictions).
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    traditional_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # train / val / test — assigned per lot by the anomaly notebook's batch-grouped
    # split, so no lot (and therefore no batch effect) crosses the boundary.
    split: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lot: Mapped["Lot"] = relationship(back_populates="components")
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )
    risk_assessments: Mapped[list["RiskAssessment"]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )
    explanations: Mapped[list["Explanation"]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parameter: Mapped[str] = mapped_column(String(32))  # Leakage / Resistance / Vth
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Ambient/Proxy stress recorded alongside the reading (spec's temperature column).
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    checkpoint_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    component: Mapped["Component"] = relationship(back_populates="measurements")

    __table_args__ = (
        UniqueConstraint("component_id", "timestamp", "parameter", name="uq_measurement_point"),
        Index("ix_measurements_component_param_time", "component_id", "parameter", "timestamp"),
    )


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True
    )
    parameter: Mapped[str] = mapped_column(String(32))
    predicted_168h: Mapped[float] = mapped_column(Float)
    actual_168h: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    component: Mapped["Component"] = relationship(back_populates="predictions")


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True
    )
    anomaly_score: Mapped[float] = mapped_column(Float)
    drift_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16))  # PASS / MONITOR / HOLD / REJECT
    confidence: Mapped[float] = mapped_column(Float)
    predicted_drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lot_relative_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    multivariate_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_lot_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_spec_fail: Mapped[int] = mapped_column(Integer, default=0)
    # v5.1 safety-slope early rejection: the predicted 168h drift rate exceeded the
    # threshold measured on Safe TRAIN rows, so the risk score is floored at 65.
    slope_reject_flag: Mapped[int] = mapped_column(Integer, default=0)
    # Top-|SHAP| anomaly features, "feat(+0.12), feat2(-0.03)" (NULL when not computed).
    shap_top_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    component: Mapped["Component"] = relationship(back_populates="risk_assessments")


class Explanation(Base):
    __tablename__ = "explanations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    module: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(String(64))
    contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    component: Mapped["Component"] = relationship(back_populates="explanations")


class InferenceRun(Base):
    __tablename__ = "inference_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False)  # SUCCESS, DEGRADED, FALLBACK
    module_a_status: Mapped[str] = mapped_column(String(32), nullable=False)   # loaded, fallback, unavailable
    module_b_status: Mapped[str] = mapped_column(String(32), nullable=False)   # loaded, degraded, unavailable
    module_c_status: Mapped[str] = mapped_column(String(32), nullable=False)   # active, unavailable
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    anomaly_pipeline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    drift_models_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    shap_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_components: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    component_links: Mapped[list["InferenceRunComponent"]] = relationship(
        back_populates="inference_run", cascade="all, delete-orphan"
    )


class InferenceRunComponent(Base):
    __tablename__ = "inference_run_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("inference_runs.run_id", ondelete="CASCADE"), index=True, nullable=False
    )
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    inference_run: Mapped["InferenceRun"] = relationship(back_populates="component_links")

    __table_args__ = (
        UniqueConstraint("run_id", "component_id", name="uq_run_component"),
    )
