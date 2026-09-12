"""
CRUD + reusable queries (Member 2).

The service layer (Member 1) calls these — they return plain dicts shaped to
match the existing Pydantic response models, so nothing above this layer needs
to know about SQLAlchemy.

Every function takes an optional trailing `db` session; when omitted, one is
opened and closed for the call. This keeps Member 1's routers session-agnostic.
"""

from contextlib import contextmanager
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import models
from database.connection import SessionLocal

# Alerts: any component with risk_score >= this threshold needs attention.
ALERT_RISK_THRESHOLD = 50.0


@contextmanager
def session_scope():
    """Yield a session, committing on success and rolling back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _session(db: Optional[Session]) -> Session:
    if db is not None:
        return db
    return SessionLocal()


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------

def create_lot(lot_id: str, component_type: str, temperature: Optional[float] = None,
               total_components: int = 0, status: str = "IN_REVIEW", db: Optional[Session] = None,
               **extra) -> models.Lot:
    s = _session(db)
    lot = models.Lot(
        lot_id=lot_id, component_type=component_type, temperature=temperature,
        total_components=total_components, status=status,
        **{k: v for k, v in extra.items() if hasattr(models.Lot, k)},
    )
    s.merge(lot)
    if db is None:
        s.commit()
    return lot


def create_component(component_id: str, lot_id: str, component_type: str,
                     status: str = "UNKNOWN", db: Optional[Session] = None,
                     **extra) -> models.Component:
    s = _session(db)
    comp = models.Component(
        component_id=component_id, lot_id=lot_id, component_type=component_type,
        status=status,
        **{k: v for k, v in extra.items() if hasattr(models.Component, k)},
    )
    s.merge(comp)
    if db is None:
        s.commit()
    return comp


def add_measurements(rows: List[dict], db: Optional[Session] = None) -> int:
    """Bulk insert measurement rows; skips exact duplicates (upsert semantics)."""
    s = _session(db)
    existing = set()
    if rows:
        stmt = select(models.Measurement.component_id, models.Measurement.timestamp,
                      models.Measurement.parameter).where(
            models.Measurement.component_id.in_({r["component_id"] for r in rows}))
        existing = {(c, t, p) for c, t, p in s.execute(stmt).all()}

    count = 0
    for r in rows:
        ts = r["timestamp"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
        key = (r["component_id"], ts, r["parameter"])
        if key in existing:
            continue
        s.add(models.Measurement(
            component_id=r["component_id"], timestamp=ts,
            parameter=r["parameter"], value=r.get("value"),
            temperature=r.get("temperature"), checkpoint_hours=r.get("checkpoint_hours"),
        ))
        count += 1
    if db is None:
        s.commit()
    return count


def add_prediction(component_id: str, parameter: str, predicted_168h: float,
                   actual_168h: Optional[float] = None,
                   interval_low: Optional[float] = None, interval_high: Optional[float] = None,
                   uncertainty_score: Optional[float] = None,
                   model_version: Optional[str] = None, db: Optional[Session] = None) -> models.Prediction:
    s = _session(db)
    pred = models.Prediction(
        component_id=component_id, parameter=parameter, predicted_168h=predicted_168h,
        actual_168h=actual_168h, interval_low=interval_low, interval_high=interval_high,
        uncertainty_score=uncertainty_score, model_version=model_version,
        prediction_error=(actual_168h - predicted_168h) if actual_168h is not None else None,
    )
    s.add(pred)
    if db is None:
        s.commit()
    return pred


def add_risk_assessment(component_id: str, anomaly_score: float, drift_score: float,
                        risk_score: float, decision: str, confidence: float,
                        db: Optional[Session] = None, **extra) -> models.RiskAssessment:
    s = _session(db)
    ra = models.RiskAssessment(
        component_id=component_id, anomaly_score=anomaly_score, drift_score=drift_score,
        risk_score=risk_score, decision=decision, confidence=confidence,
        **{k: v for k, v in extra.items() if hasattr(models.RiskAssessment, k)},
    )
    s.add(ra)
    if db is None:
        s.commit()
    return ra


def add_explanations(component_id: str, reasons: List[tuple],
                     db: Optional[Session] = None) -> int:
    """Insert explanation rows: list of (feature, contribution, reason)."""
    s = _session(db)
    for feature, contribution, reason in reasons:
        s.add(models.Explanation(
            component_id=component_id, feature=feature,
            contribution=contribution, reason=reason,
        ))
    if db is None:
        s.commit()
    return len(reasons)


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

def update_component_snapshot(component_id: str, db: Optional[Session] = None,
                              **fields) -> bool:
    """Refresh a component's cached screening snapshot (status/risk_level/scores)."""
    s = _session(db)
    comp = s.get(models.Component, component_id)
    if comp is None:
        return False
    for k, v in fields.items():
        if hasattr(comp, k):
            setattr(comp, k, v)
    if db is None:
        s.commit()
    return True


def update_lot_counts(lot_id: str, db: Optional[Session] = None) -> int:
    """Recompute total_components for a lot from its children."""
    s = _session(db)
    lot = s.get(models.Lot, lot_id)
    if lot is None:
        return 0
    count = s.scalar(
        select(func.count(models.Component.component_id)).where(models.Component.lot_id == lot_id)
    )
    lot.total_components = int(count or 0)
    if db is None:
        s.commit()
    return int(count or 0)


# ---------------------------------------------------------------------------
# Deletes
# ---------------------------------------------------------------------------

def delete_component(component_id: str, db: Optional[Session] = None) -> bool:
    """Delete a component and its measurements/predictions/risk/explanations."""
    s = _session(db)
    comp = s.get(models.Component, component_id)
    if comp is None:
        return False
    s.delete(comp)
    if db is None:
        s.commit()
    else:
        s.flush()  # make the delete visible inside the caller's transaction
    return True


def delete_lot(lot_id: str, db: Optional[Session] = None) -> bool:
    """Delete a lot and (via cascade) all its components."""
    s = _session(db)
    lot = s.get(models.Lot, lot_id)
    if lot is None:
        return False
    s.delete(lot)
    if db is None:
        s.commit()
    else:
        s.flush()
    return True


# ---------------------------------------------------------------------------
# Queries — the six reusable functions Member 1's APIs call
# ---------------------------------------------------------------------------

def get_lot(lot_id: str, db: Optional[Session] = None) -> Optional[dict]:
    s = _session(db)
    lot = s.get(models.Lot, lot_id)
    if lot is None:
        return None
    return {
        "lot_id": lot.lot_id,
        "total_components": lot.total_components,
        "status": lot.status,
    }


def list_lots(db: Optional[Session] = None) -> List[dict]:
    s = _session(db)
    lots = s.scalars(select(models.Lot).order_by(models.Lot.lot_id)).all()
    return [{
        "lot_id": lot.lot_id,
        "total_components": lot.total_components,
        "status": lot.status,
    } for lot in lots]


def get_lot_summary(lot_id: str, db: Optional[Session] = None) -> Optional[dict]:
    """Dashboard summary: PASS/MONITOR/HOLD/REJECT counts + avg anomaly + high-risk."""
    s = _session(db)
    lot = s.get(models.Lot, lot_id)
    if lot is None:
        return None

    comps = s.scalars(select(models.Component).where(models.Component.lot_id == lot_id)).all()
    total = len(comps)

    def count_status(decisions: tuple) -> int:
        return sum(1 for c in comps if (c.decision or "").upper() in decisions)

    passed = count_status(("PASS",))
    monitor = count_status(("MONITOR",))
    hold = count_status(("HOLD",))
    reject = count_status(("REJECT",))

    anomaly_scores = [c.anomaly_score for c in comps if c.anomaly_score is not None]
    avg_anomaly = round(sum(anomaly_scores) / len(anomaly_scores), 1) if anomaly_scores else 0.0
    high_risk = sum(1 for c in comps if (c.risk_score or 0) >= 60.0)

    return {
        "lot_id": lot_id,
        "total_components": total,
        "passed": passed,
        "monitor": monitor,
        "hold": hold,
        "reject": reject,
        "average_anomaly_score": avg_anomaly,
        "high_risk_components": high_risk,
    }


def get_lot_components(lot_id: str, db: Optional[Session] = None) -> List[dict]:
    """All components belonging to a lot, ordered by component_id."""
    s = _session(db)
    comps = s.scalars(
        select(models.Component)
        .where(models.Component.lot_id == lot_id)
        .order_by(models.Component.component_id)
    ).all()
    results = []
    for comp in comps:
        pred = s.scalars(
            select(models.Prediction).where(models.Prediction.component_id == comp.component_id)
            .order_by(models.Prediction.id.desc())
        ).first()
        ra = s.scalars(
            select(models.RiskAssessment).where(models.RiskAssessment.component_id == comp.component_id)
            .order_by(models.RiskAssessment.id.desc())
        ).first()
        slope_flag = None
        rel_tier = None
        drift_score = None
        model_version = None
        if ra is not None:
            slope_flag = bool(ra.slope_reject_flag) if ra.slope_reject_flag is not None else None
            rel_tier = ra.reliability_tier
            drift_score = ra.drift_score
            model_version = ra.model_version
        if model_version is None and pred is not None:
            model_version = pred.model_version
        results.append({
            "component_id": comp.component_id,
            "lot_id": comp.lot_id,
            "status": comp.status,
            "risk_level": comp.risk_level or "UNKNOWN",
            "anomaly_score": comp.anomaly_score,
            "predicted_168h": pred.predicted_168h if pred else None,
            "risk_score": comp.risk_score,
            "decision": comp.decision,
            "confidence": comp.confidence,
            "slope_reject_flag": slope_flag,
            "reliability_tier": rel_tier,
            "reliability_index": comp.reliability_index,
            "split": comp.split,
            "drift_score": drift_score,
            "model_version": model_version,
        })
    return results


def get_component(component_id: str, db: Optional[Session] = None) -> Optional[dict]:
    """One component with its latest screening snapshot (schema: ComponentResponse)."""
    s = _session(db)
    comp = s.get(models.Component, component_id)
    if comp is None:
        return None
    pred = s.scalars(
        select(models.Prediction).where(models.Prediction.component_id == component_id)
        .order_by(models.Prediction.id.desc())
    ).first()
    return {
        "component_id": comp.component_id,
        "lot_id": comp.lot_id,
        "status": comp.status,
        "risk_level": comp.risk_level or "UNKNOWN",
        "anomaly_score": comp.anomaly_score,
        "predicted_168h": pred.predicted_168h if pred else None,
        "risk_score": comp.risk_score,
        "decision": comp.decision,
        "confidence": comp.confidence,
        "reliability_index": comp.reliability_index,
        "split": comp.split,
    }


def get_component_measurements(component_id: str, parameter: Optional[str] = None,
                               db: Optional[Session] = None) -> List[dict]:
    """All measurements for a component (optionally one parameter), time-ordered."""
    s = _session(db)
    stmt = select(models.Measurement).where(models.Measurement.component_id == component_id)
    if parameter:
        stmt = stmt.where(models.Measurement.parameter == parameter)
    stmt = stmt.order_by(models.Measurement.parameter, models.Measurement.timestamp)
    rows = s.scalars(stmt).all()
    return [{
        "id": m.id,
        "component_id": m.component_id,
        "timestamp": m.timestamp,
        "parameter": m.parameter,
        "value": m.value,
        "temperature": m.temperature,
        "checkpoint_hours": m.checkpoint_hours,
    } for m in rows]


def get_component_prediction(component_id: str, parameter: Optional[str] = None,
                             db: Optional[Session] = None) -> Optional[dict]:
    """Latest prediction row (optionally one parameter)."""
    s = _session(db)
    stmt = select(models.Prediction).where(models.Prediction.component_id == component_id)
    if parameter:
        stmt = stmt.where(models.Prediction.parameter == parameter)
    pred = s.scalars(stmt.order_by(models.Prediction.id.desc())).first()
    if pred is None:
        return None
    return {
        "id": pred.id,
        "component_id": pred.component_id,
        "parameter": pred.parameter,
        "predicted_168h": pred.predicted_168h,
        "actual_168h": pred.actual_168h,
        "prediction_error": pred.prediction_error,
        "interval_low": pred.interval_low,
        "interval_high": pred.interval_high,
        "uncertainty_score": pred.uncertainty_score,
        "model_version": pred.model_version,
    }


def get_component_risk(component_id: str, db: Optional[Session] = None) -> Optional[dict]:
    """Latest risk assessment for a component."""
    s = _session(db)
    ra = s.scalars(
        select(models.RiskAssessment).where(models.RiskAssessment.component_id == component_id)
        .order_by(models.RiskAssessment.id.desc())
    ).first()
    if ra is None:
        return None
    return {
        "id": ra.id,
        "component_id": ra.component_id,
        "anomaly_score": ra.anomaly_score,
        "drift_score": ra.drift_score,
        "risk_score": ra.risk_score,
        "decision": ra.decision,
        "confidence": ra.confidence,
        "predicted_drift_score": ra.predicted_drift_score,
        "reliability_index": ra.reliability_index,
        "reliability_tier": ra.reliability_tier,
        "lot_relative_score": ra.lot_relative_score,
        "multivariate_score": ra.multivariate_score,
        "worst_lot_zscore": ra.worst_lot_zscore,
        "absolute_spec_fail": ra.absolute_spec_fail,
        "slope_reject_flag": ra.slope_reject_flag,
        "shap_top_features": ra.shap_top_features,
        "model_version": ra.model_version,
    }


def get_alerts(min_risk_score: float = ALERT_RISK_THRESHOLD,
               db: Optional[Session] = None) -> List[dict]:
    """High-risk components requiring attention (schema: AlertResponse)."""
    s = _session(db)
    comps = s.scalars(
        select(models.Component)
        .where(models.Component.risk_score >= min_risk_score)
        .order_by(models.Component.risk_score.desc())
    ).all()

    def severity(score: float) -> str:
        if score >= 80:
            return "CRITICAL"
        if score >= 60:
            return "HIGH"
        return "MEDIUM"

    return [{
        "component_id": c.component_id,
        "lot_id": c.lot_id,
        "risk_score": c.risk_score or 0.0,
        "severity": severity(c.risk_score or 0.0),
    } for c in comps]


# ---------------------------------------------------------------------------
# Report data preparation (Member 2 responsibility #6)
# ---------------------------------------------------------------------------

def get_component_trajectory(component_id: str, parameter: Optional[str] = None,
                             db: Optional[Session] = None) -> Optional[dict]:
    """
    Chart-ready actual/predicted trajectory for a component.
    Actual points come from stored measurements (elapsed hours on the x-axis);
    predicted points come from Module B's 168h forecast for the same parameter.
    """
    s = _session(db)
    comp = s.get(models.Component, component_id)
    if comp is None:
        return None

    if parameter is None:
        # Pick the parameter with the widest observed early drift.
        measurements = s.scalars(
            select(models.Measurement).where(models.Measurement.component_id == component_id)
        ).all()
        drift_by_param: Dict[str, float] = {}
        by_param: Dict[str, Dict[float, float]] = {}
        for m in measurements:
            by_param.setdefault(m.parameter, {})[m.checkpoint_hours or 0] = m.value
        for p, pts in by_param.items():
            if 0 in pts and 24 in pts and pts.get(0):
                drift_by_param[p] = abs((pts[24] - pts[0]) / pts[0])
        parameter = max(drift_by_param, key=drift_by_param.get) if drift_by_param else "Leakage"

    actual_rows = s.scalars(
        select(models.Measurement)
        .where(models.Measurement.component_id == component_id,
               models.Measurement.parameter == parameter)
        .order_by(models.Measurement.timestamp)
    ).all()
    actual = [{"time": m.checkpoint_hours or 0, "value": m.value}
              for m in actual_rows if m.value is not None]

    pred = s.scalars(
        select(models.Prediction)
        .where(models.Prediction.component_id == component_id,
               models.Prediction.parameter == parameter)
        .order_by(models.Prediction.id.desc())
    ).first()
    predicted = []
    if pred is not None:
        predicted.append({"time": 168, "value": pred.predicted_168h})
        if pred.interval_low is not None and pred.interval_high is not None:
            predicted.append({"time": 168, "value_low": pred.interval_low,
                              "value_high": pred.interval_high})

    # Safety envelope: datasheet bound for the tracked parameter (Module C logic).
    safety_limits = {
        "Leakage": 3.0,       # µA — must stay at/below
        "Resistance": 120.0,  # Ω — upper datasheet bound
        "Vth": 1.40,          # V — upper datasheet bound
    }
    return {
        "component_id": component_id,
        "parameter": parameter,
        "actual": actual,
        "predicted": predicted,
        "safety_limit": safety_limits.get(parameter),
    }


def get_component_report(component_id: str, db: Optional[Session] = None) -> Optional[dict]:
    """
    Complete report data for one component (spec responsibility #6):

        component information + measurements + prediction + risk assessment
        + explanation  →  complete report data

    Shape mirrors `ComponentReportResponse` plus extra detail keys.
    """
    s = _session(db)
    comp = s.get(models.Component, component_id)
    if comp is None:
        return None

    measurements = get_component_measurements(component_id, db=s)
    risk = get_component_risk(component_id, db=s)
    pred = get_component_prediction(component_id, db=s)
    explanations = s.scalars(
        select(models.Explanation).where(models.Explanation.component_id == component_id)
        .order_by(models.Explanation.id)
    ).all()

    # Summarize measurements per parameter for the report body.
    meas_summary: Dict[str, dict] = {}
    for m in measurements:
        p = m["parameter"]
        entry = meas_summary.setdefault(p, {"first": None, "last": None, "min": None,
                                            "max": None, "drift_pct": None})
        v = m["value"]
        if v is None:
            continue
        entry["first"] = v if entry["first"] is None else entry["first"]
        entry["last"] = v
        entry["min"] = v if entry["min"] is None else min(entry["min"], v)
        entry["max"] = v if entry["max"] is None else max(entry["max"], v)
    for p, e in meas_summary.items():
        if e["first"] and e["first"] != 0:
            e["drift_pct"] = round((e["last"] - e["first"]) / abs(e["first"]) * 100, 1)

    drift_risk = "LOW"
    if risk is not None:
        if risk.get("drift_score", 0) >= 60 or risk.get("uncertainty_score", 0) >= 60:
            drift_risk = "HIGH"
        elif risk.get("drift_score", 0) >= 30:
            drift_risk = "MEDIUM"

    reasons = [e.reason for e in explanations] or ["No significant deviation detected."]

    return {
        "component_id": comp.component_id,
        "lot_id": comp.lot_id,
        "anomaly_score": comp.anomaly_score,
        "drift_risk": drift_risk,
        "predicted_168h": pred["predicted_168h"] if pred else None,
        "risk_score": comp.risk_score,
        "decision": comp.decision or "PASS",
        "confidence": comp.confidence,
        "reasons": reasons,
        "model_version": pred["model_version"] if pred else None,
        # Extended detail (Member 2's "complete report data").
        "reliability_index": comp.reliability_index,
        "reliability_tier": (risk or {}).get("reliability_tier"),
        "slope_reject_flag": (risk or {}).get("slope_reject_flag"),
        "split": comp.split,
        "component_type": comp.component_type,
        "status": comp.status,
        "traditional_result": comp.traditional_result,
        "label": comp.label,
        "measurements": measurements,
        "measurement_summary": meas_summary,
        "prediction": pred,
        "risk_assessment": risk,
        "explanations": [{
            "feature": e.feature,
            "contribution": e.contribution,
            "reason": e.reason,
        } for e in explanations],
    }


def count_rows(db: Optional[Session] = None) -> Dict[str, int]:
    s = _session(db)
    return {
        "lots": s.scalar(select(func.count()).select_from(models.Lot)) or 0,
        "components": s.scalar(select(func.count()).select_from(models.Component)) or 0,
        "measurements": s.scalar(select(func.count()).select_from(models.Measurement)) or 0,
        "predictions": s.scalar(select(func.count()).select_from(models.Prediction)) or 0,
        "risk_assessments": s.scalar(select(func.count()).select_from(models.RiskAssessment)) or 0,
        "explanations": s.scalar(select(func.count()).select_from(models.Explanation)) or 0,
    }
