"""
Component service layer.

Same boundary contract as lot_service.py: routers call these functions only.
Database-first: queries Member 2's database layer via CRUD, enriches with
RiskAssessment fields, and falls back to mock data when records are not found.
"""

from typing import Optional

from app.services import mock_data
from database import crud
from database.connection import init_db

# Ensure database tables exist if running against a fresh database file.
try:
    init_db()
except Exception:
    pass


def get_component(component_id: str) -> Optional[dict]:
    comp = crud.get_component(component_id)
    if comp is not None:
        risk = crud.get_component_risk(component_id)
        if risk is not None:
            raw_slope = risk.get("slope_reject_flag")
            comp["slope_reject_flag"] = bool(raw_slope) if raw_slope is not None else None
            comp["reliability_tier"] = risk.get("reliability_tier")
        else:
            comp.setdefault("slope_reject_flag", None)
            comp.setdefault("reliability_tier", None)
        return comp
    return mock_data.MOCK_COMPONENTS.get(component_id)


def _adapt_predicted_point(pt: dict) -> Optional[dict]:
    """
    Ensure each predicted trajectory point satisfies the TrajectoryPoint schema
    (requires time and value). If value is missing but interval bounds
    (value_low, value_high) exist, calculate the midpoint.
    """
    if not isinstance(pt, dict):
        return None

    adapted = dict(pt)
    val = pt.get("value")
    if val is not None:
        try:
            adapted["value"] = float(val)
            return adapted
        except (TypeError, ValueError):
            pass

    low = pt.get("value_low")
    high = pt.get("value_high")
    if low is not None and high is not None:
        try:
            f_low = float(low)
            f_high = float(high)
            adapted["value"] = (f_low + f_high) / 2.0
            return adapted
        except (TypeError, ValueError):
            pass

    return None


def get_trajectory(component_id: str) -> Optional[dict]:
    trajectory = crud.get_component_trajectory(component_id)
    if trajectory is not None:
        adapted_trajectory = dict(trajectory)
        if "predicted" in adapted_trajectory and isinstance(adapted_trajectory["predicted"], list):
            adapted_predicted = []
            for pt in adapted_trajectory["predicted"]:
                adapted_pt = _adapt_predicted_point(pt)
                if adapted_pt is not None:
                    adapted_predicted.append(adapted_pt)
            adapted_trajectory["predicted"] = adapted_predicted
        return adapted_trajectory

    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    return mock_data.MOCK_TRAJECTORIES.get(
        component_id, mock_data.get_default_trajectory(component_id)
    )


def get_report(component_id: str) -> Optional[dict]:
    report = crud.get_component_report(component_id)
    if report is not None:
        return report
    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    return mock_data.MOCK_REPORTS.get(
        component_id, mock_data.get_default_report(component_id)
    )
