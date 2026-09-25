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
    try:
        with crud.session_scope() as s:
            comp = crud.get_component(component_id, db=s)
            if comp is not None:
                risk = crud.get_component_risk(component_id, db=s)
                if risk is not None:
                    raw_slope = risk.get("slope_reject_flag")
                    comp["slope_reject_flag"] = bool(raw_slope) if raw_slope is not None else None
                    comp["reliability_tier"] = risk.get("reliability_tier")
                    comp["drift_score"] = risk.get("drift_score")
                    comp["model_version"] = risk.get("model_version")
                    if comp.get("reliability_index") is None:
                        comp["reliability_index"] = risk.get("reliability_index")
                else:
                    comp.setdefault("slope_reject_flag", None)
                    comp.setdefault("reliability_tier", None)
                    comp.setdefault("drift_score", None)
                    comp.setdefault("model_version", None)

                if comp.get("model_version") is None:
                    pred = crud.get_component_prediction(component_id, db=s)
                    if pred and pred.get("model_version"):
                        comp["model_version"] = pred.get("model_version")

                # Phase 17: attach inference run traceability if linked
                run = crud.get_latest_inference_run_for_component(component_id, db=s)
                if run is not None:
                    comp["inference_run_id"] = run["run_id"]
                    comp["inference_trace"] = {
                        "run_id": run["run_id"],
                        "timestamp": run["timestamp"],
                        "model_version": run["model_version"],
                        "execution_status": run["execution_status"],
                        "module_a_status": run["module_a_status"],
                        "module_b_status": run["module_b_status"],
                        "module_c_status": run["module_c_status"],
                        "is_fallback": run["is_fallback"],
                        "artifact_hashes": {
                            "anomaly_pipeline.joblib": run["anomaly_pipeline_hash"],
                            "drift_prediction_models.joblib": run["drift_models_hash"],
                            "anomaly_shap_bundle.joblib": run["shap_bundle_hash"],
                            "config.json": run["config_hash"],
                        },
                        "config_hash": run["config_hash"],
                        "source_filename": run["source_filename"],
                        "total_components": run["total_components"],
                    }
                else:
                    comp["inference_run_id"] = None
                    comp["inference_trace"] = None

                return comp
    except Exception:
        pass
    mock = mock_data.MOCK_COMPONENTS.get(component_id)
    if mock is not None:
        mock = dict(mock)
        mock.setdefault("inference_run_id", None)
        mock.setdefault("inference_trace", None)
    return mock


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
    try:
        with crud.session_scope() as s:
            trajectory = crud.get_component_trajectory(component_id, db=s)
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
    except Exception:
        pass

    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    return mock_data.MOCK_TRAJECTORIES.get(
        component_id, mock_data.get_default_trajectory(component_id)
    )


def get_report(component_id: str) -> Optional[dict]:
    try:
        with crud.session_scope() as s:
            report = crud.get_component_report(component_id, db=s)
            if report is not None:
                rep = dict(report)
                risk = rep.get("risk_assessment") or {}
                pred = rep.get("prediction") or {}

                # Pass through ML fields to the top-level response schema
                if "drift_score" not in rep or rep["drift_score"] is None:
                    rep["drift_score"] = risk.get("drift_score")
                if "predicted_drift_score" not in rep or rep["predicted_drift_score"] is None:
                    rep["predicted_drift_score"] = risk.get("predicted_drift_score")
                if "reliability_index" not in rep or rep["reliability_index"] is None:
                    rep["reliability_index"] = risk.get("reliability_index")
                if "lot_relative_score" not in rep or rep["lot_relative_score"] is None:
                    rep["lot_relative_score"] = risk.get("lot_relative_score")
                if "multivariate_score" not in rep or rep["multivariate_score"] is None:
                    rep["multivariate_score"] = risk.get("multivariate_score")
                if "worst_lot_zscore" not in rep or rep["worst_lot_zscore"] is None:
                    rep["worst_lot_zscore"] = risk.get("worst_lot_zscore")
                if "absolute_spec_fail" not in rep or rep["absolute_spec_fail"] is None:
                    rep["absolute_spec_fail"] = risk.get("absolute_spec_fail")

                if "interval_low" not in rep or rep["interval_low"] is None:
                    rep["interval_low"] = pred.get("interval_low")
                if "interval_high" not in rep or rep["interval_high"] is None:
                    rep["interval_high"] = pred.get("interval_high")
                if "uncertainty_score" not in rep or rep["uncertainty_score"] is None:
                    rep["uncertainty_score"] = pred.get("uncertainty_score")

                if not rep.get("model_version"):
                    rep["model_version"] = risk.get("model_version") or pred.get("model_version")

                # Phase 17: attach inference run traceability if linked
                run = crud.get_latest_inference_run_for_component(component_id, db=s)
                if run is not None:
                    rep["inference_run_id"] = run["run_id"]
                    rep["inference_trace"] = {
                        "run_id": run["run_id"],
                        "timestamp": run["timestamp"],
                        "model_version": run["model_version"],
                        "execution_status": run["execution_status"],
                        "module_a_status": run["module_a_status"],
                        "module_b_status": run["module_b_status"],
                        "module_c_status": run["module_c_status"],
                        "is_fallback": run["is_fallback"],
                        "artifact_hashes": {
                            "anomaly_pipeline.joblib": run["anomaly_pipeline_hash"],
                            "drift_prediction_models.joblib": run["drift_models_hash"],
                            "anomaly_shap_bundle.joblib": run["shap_bundle_hash"],
                            "config.json": run["config_hash"],
                        },
                        "config_hash": run["config_hash"],
                        "source_filename": run["source_filename"],
                        "total_components": run["total_components"],
                    }
                else:
                    rep["inference_run_id"] = None
                    rep["inference_trace"] = None

                # Phase 19: attach human engineering review disposition if available
                try:
                    from app.services import review_service
                    rev_res = review_service.get_component_reviews(component_id)
                    if rev_res is not None:
                        curr_disp, total_revs, rev_list = rev_res
                        rep["current_disposition"] = curr_disp
                        rep["total_reviews"] = total_revs
                        rep["reviews"] = [
                            {
                                "id": r.id,
                                "component_id": r.component_id,
                                "user_id": r.user_id,
                                "username": r.username,
                                "role": r.role,
                                "disposition": r.disposition,
                                "comment": r.comment,
                                "created_at": r.created_at,
                                "updated_at": r.updated_at,
                            }
                            for r in rev_list
                        ]
                    else:
                        rep["current_disposition"] = None
                        rep["total_reviews"] = 0
                        rep["reviews"] = []
                except Exception:
                    rep["current_disposition"] = None
                    rep["total_reviews"] = 0
                    rep["reviews"] = []

                return rep
    except Exception:
        pass

    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    mock = mock_data.MOCK_REPORTS.get(
        component_id, mock_data.get_default_report(component_id)
    )
    if mock is not None:
        mock = dict(mock)
        mock.setdefault("current_disposition", None)
        mock.setdefault("total_reviews", 0)
        mock.setdefault("reviews", [])
    return mock
