"""
DEMO / MOCK DATA — NOT REAL MEASURED DATA
==========================================

This module exists purely so the API layer (Member 1) has something
concrete to return while building and testing endpoints, and so the
frontend can be developed against a stable contract before the
database layer (Member 2) is ready.

Nothing in this file should be treated as an actual measurement,
model output, or production value.

INTEGRATION POINT FOR MEMBER 2:
Every function in `lot_service.py`, `component_service.py`, and
`alert_service.py` reads from the dictionaries below. To plug in
PostgreSQL, replace the bodies of those service functions with real
SQLAlchemy queries — the API routers only call the service functions
and never touch this module directly, so no router code needs to
change.
"""

from typing import Dict, List

# --- Demo lots -------------------------------------------------------------

MOCK_LOTS: Dict[str, dict] = {
    "LOT_27": {
        "lot_id": "LOT_27",
        "total_components": 4,
        "status": "IN_REVIEW",
    },
    "LOT_31": {
        "lot_id": "LOT_31",
        "total_components": 2,
        "status": "CLEARED",
    },
}

MOCK_LOT_SUMMARIES: Dict[str, dict] = {
    "LOT_27": {
        "lot_id": "LOT_27",
        "total_components": 4,
        "passed": 1,
        "monitor": 1,
        "hold": 1,
        "reject": 1,
        "average_anomaly_score": 42.5,
        "high_risk_components": 2,
    },
    "LOT_31": {
        "lot_id": "LOT_31",
        "total_components": 2,
        "passed": 2,
        "monitor": 0,
        "hold": 0,
        "reject": 0,
        "average_anomaly_score": 8.1,
        "high_risk_components": 0,
    },
}

# --- Demo components ---------------------------------------------------------

MOCK_COMPONENTS: Dict[str, dict] = {
    "C101": {
        "component_id": "C101",
        "lot_id": "LOT_27",
        "status": "PASS",
        "risk_level": "LOW",
        "anomaly_score": 12.4,
        "predicted_168h": 20.1,
        "risk_score": 15.0,
        "decision": "PASS",
        "confidence": 0.94,
        "slope_reject_flag": False,
        "reliability_tier": "Space-Safe",
        "drift_score": 10.5,
        "reliability_index": 92.0,
        "model_version": "sage-1.1",
        "split": "train",
    },
    "C102": {
        "component_id": "C102",
        "lot_id": "LOT_27",
        "status": "MONITOR",
        "risk_level": "MEDIUM",
        "anomaly_score": 38.7,
        "predicted_168h": 41.0,
        "risk_score": 52.0,
        "decision": "MONITOR",
        "confidence": 0.81,
        "slope_reject_flag": False,
        "reliability_tier": "Borderline",
    },
    "C103": {
        "component_id": "C103",
        "lot_id": "LOT_27",
        "status": "HOLD",
        "risk_level": "MEDIUM",
        "anomaly_score": 55.2,
        "predicted_168h": 44.6,
        "risk_score": 68.0,
        "decision": "HOLD",
        "confidence": 0.77,
        "slope_reject_flag": False,
        "reliability_tier": "Borderline",
    },
    "C104": {
        "component_id": "C104",
        "lot_id": "LOT_27",
        "status": "REJECT",
        "risk_level": "CRITICAL",
        "anomaly_score": 81.0,
        "predicted_168h": 48.7,
        "risk_score": 87.0,
        "decision": "REJECT",
        "confidence": 0.90,
        "slope_reject_flag": True,
        "reliability_tier": "High-Risk",
        "drift_score": 78.5,
        "reliability_index": 18.0,
        "model_version": "sage-1.1",
        "split": "test",
    },
    "C201": {
        "component_id": "C201",
        "lot_id": "LOT_31",
        "status": "PASS",
        "risk_level": "LOW",
        "anomaly_score": 6.0,
        "predicted_168h": 9.5,
        "risk_score": 5.0,
        "decision": "PASS",
        "confidence": 0.97,
        "slope_reject_flag": False,
        "reliability_tier": "Space-Safe",
    },
    "C202": {
        "component_id": "C202",
        "lot_id": "LOT_31",
        "status": "PASS",
        "risk_level": "LOW",
        "anomaly_score": 9.1,
        "predicted_168h": 11.2,
        "risk_score": 7.0,
        "decision": "PASS",
        "confidence": 0.96,
        "slope_reject_flag": False,
        "reliability_tier": "Space-Safe",
    },
}

MOCK_TRAJECTORIES: Dict[str, dict] = {
    "C104": {
        "component_id": "C104",
        "actual": [
            {"time": 0, "value": 10},
            {"time": 24, "value": 25},
            {"time": 48, "value": 33},
            {"time": 72, "value": 41},
        ],
        "predicted": [
            {"time": 96, "value": 37},
            {"time": 120, "value": 42.5},
            {"time": 168, "value": 48.7},
        ],
        "safety_limit": 45,
    },
}

MOCK_REPORTS: Dict[str, dict] = {
    "C104": {
        "component_id": "C104",
        "lot_id": "LOT_27",
        "anomaly_score": 81.0,
        "drift_risk": "HIGH",
        "predicted_168h": 48.7,
        "risk_score": 87.0,
        "decision": "REJECT",
        "confidence": 0.90,
        "reasons": [
            "Batch-relative outlier (z=9.61 vs its own lot)",
            "[SHAP] Anomaly score influenced by Leakage_delta_24h(+0.04), Leakage_pct_change_24h(+0.03) (SHAP attribution)",
            "[SHAP] Predicted Leakage 168h drift influenced by Leakage_slope(+0.582), Vth_slope(+0.151) (SHAP attribution)",
            "Predicted 168h drift rate exceeds the Safe-population safety slope — flagged for early rejection",
        ],
        "model_version": "sage-1.1",
        "slope_reject_flag": True,
        "reliability_tier": "High-Risk",
        "drift_score": 78.5,
        "predicted_drift_score": 82.0,
        "reliability_index": 18.0,
        "lot_relative_score": 9.61,
        "multivariate_score": 81.0,
        "worst_lot_zscore": 9.61,
        "absolute_spec_fail": 1,
        "interval_low": 45.0,
        "interval_high": 52.0,
        "uncertainty_score": 15.0,
        "explanations": [
            {
                "feature": "Leakage_slope",
                "contribution": 0.582,
                "reason": "Predicted Leakage 168h drift influenced by Leakage_slope (+0.582 SHAP attribution).",
                "module": "MODULE_B",
                "evidence_type": "SHAP",
            },
            {
                "feature": "Lot_Relative",
                "contribution": 9.61,
                "reason": "Batch-relative outlier (z=9.61 vs its own lot)",
                "module": "MODULE_A",
                "evidence_type": "DRIFT",
            },
        ],
    },
}


def get_default_trajectory(component_id: str) -> dict:
    """Fallback trajectory shape for components without curated demo data."""
    return {
        "component_id": component_id,
        "actual": [{"time": 0, "value": 5}, {"time": 24, "value": 8}],
        "predicted": [{"time": 168, "value": 10}],
        "safety_limit": 45,
    }


def get_default_report(component_id: str) -> dict:
    """Fallback report shape for components without curated demo data."""
    component = MOCK_COMPONENTS.get(component_id)
    if not component:
        return None
    return {
        "component_id": component_id,
        "lot_id": component["lot_id"],
        "anomaly_score": component.get("anomaly_score"),
        "drift_risk": "LOW",
        "predicted_168h": component.get("predicted_168h"),
        "risk_score": component.get("risk_score"),
        "decision": component.get("decision", "PASS"),
        "confidence": component.get("confidence"),
        "reasons": ["No significant deviation detected"],
        "model_version": "sage-1.1",
        "slope_reject_flag": component.get("slope_reject_flag", False),
        "reliability_tier": component.get("reliability_tier", "Space-Safe"),
    }


def list_alerts() -> List[dict]:
    """High-risk components requiring attention, derived from mock components."""
    severity_by_score = lambda score: (
        "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM"
    )
    alerts = []
    for c in MOCK_COMPONENTS.values():
        if c.get("risk_score", 0) >= 50:
            alerts.append(
                {
                    "component_id": c["component_id"],
                    "lot_id": c["lot_id"],
                    "risk_score": c["risk_score"],
                    "severity": severity_by_score(c["risk_score"]),
                }
            )
    return alerts
