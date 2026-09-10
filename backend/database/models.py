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
- ``measurements``    : long format — one row per (component, timestamp, parameter)
                        for Leakage (µA), Resistance (Ω), Vth (V).
- ``predictions``     : Module B drift-forecast output (predicted_168h / actual_168h
                        / prediction_error per parameter).
- ``risk_assessments``: fused risk from Modules A + B (anomaly, drift, risk scores,
                        decision, confidence).
- ``explanations``    : per-feature contribution rows backing the report reasons.
"""

from datetime import datetime

from sqlalchemy import (
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
    lot_relative_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    multivariate_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_lot_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_spec_fail: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    component: Mapped["Component"] = relationship(back_populates="risk_assessments")


class Explanation(Base):
    __tablename__ = "explanations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.component_id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(String(64))
    contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    component: Mapped["Component"] = relationship(back_populates="explanations")
