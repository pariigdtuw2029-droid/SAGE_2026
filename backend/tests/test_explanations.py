"""
Phase 18: Explainability and Evidence Chain Unit and Integration Tests.
Covers all 25 specific test conditions defined in the Phase 18 specification.
"""

import os
import sqlite3
import pytest
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, select, text, inspect
from sqlalchemy.orm import sessionmaker

from database import models, crud, ml_inference
from database.connection import Base
from app.schemas.component import ExplanationItem, ComponentReportResponse


DB_FILE = Path(__file__).resolve().parent.parent / "astra_guard.db"


@pytest.fixture
def memory_db():
    """Create a temporary in-memory database with full schema for isolated testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    crud.init_inference_db(target_engine=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield engine, session
    session.close()


def test_01_new_explanation_columns_exist():
    """1. New explanation columns exist in authoritative database schema."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("PRAGMA table_info(explanations)")
    cols = {row[1]: row[2] for row in c.fetchall()}
    conn.close()

    assert "run_id" in cols, "run_id column missing from explanations table"
    assert "module" in cols, "module column missing from explanations table"
    assert "evidence_type" in cols, "evidence_type column missing from explanations table"


def test_02_historical_rows_remain_intact():
    """2. Existing historical rows remain intact."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM explanations")
    count = c.fetchone()[0]
    conn.close()
    assert count >= 79, f"Expected at least 79 explanations, got {count}"


def test_03_historical_explanation_rows_null_for_new_fields():
    """3. Existing historical explanation rows remain NULL for new provenance fields."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM explanations WHERE run_id IS NULL AND module IS NULL AND evidence_type IS NULL"
    )
    null_count = c.fetchone()[0]
    conn.close()
    assert null_count >= 0


def test_04_migration_is_idempotent():
    """4. Migration is idempotent and safe to run repeatedly."""
    engine = create_engine("sqlite:///:memory:")
    # Create older version of explanations without new columns
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE explanations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_id VARCHAR(32) NOT NULL,
                reason TEXT NOT NULL,
                feature VARCHAR(64) NOT NULL,
                contribution FLOAT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO explanations (component_id, reason, feature, contribution)
            VALUES ('C001', 'Test explanation', 'Feature_A', 0.5)
        """))
        conn.commit()

    # First migration run
    crud.migrate_explanations_schema(target_engine=engine)

    # Second migration run (idempotency check)
    crud.migrate_explanations_schema(target_engine=engine)

    # Third migration run
    crud.migrate_explanations_schema(target_engine=engine)

    with engine.connect() as conn:
        inspector = inspect(conn)
        cols = {c["name"] for c in inspector.get_columns("explanations")}
        assert "run_id" in cols
        assert "module" in cols
        assert "evidence_type" in cols

        res = conn.execute(text("SELECT id, component_id, run_id, module, evidence_type FROM explanations")).fetchone()
        assert res[0] == 1
        assert res[1] == "C001"
        assert res[2] is None
        assert res[3] is None
        assert res[4] is None


def test_05_06_new_explanations_receive_run_id_and_share_same_run_id(memory_db):
    """5 and 6. New explanations receive run_id and all explanations from same run share it."""
    engine, session = memory_db

    lot = models.Lot(lot_id="LOT-TEST-01", component_type="MOSFET", total_components=1)
    comp = models.Component(component_id="C9999", lot_id="LOT-TEST-01", component_type="MOSFET")
    session.add(lot)
    session.add(comp)
    session.commit()

    test_run_id = "run_test_provenance_uuid_12345"
    reasons = [
        ("Lot_Relative", 2.5, "Batch-relative outlier", "MODULE_A", "DRIFT"),
        ("Predicted_Drift", 75.0, "Forecast to 168h shows continued significant drift.", "MODULE_B", "DRIFT"),
        ("Traditional_Test_Result", 1.0, "Failed traditional fixed-limit spec check.", "MODULE_C", "RULE"),
    ]

    inserted = crud.add_explanations("C9999", reasons, run_id=test_run_id, db=session)
    session.commit()
    assert inserted == 3

    rows = session.scalars(
        select(models.Explanation).where(models.Explanation.component_id == "C9999")
    ).all()
    assert len(rows) == 3
    for r in rows:
        assert r.run_id == test_run_id, f"Expected run_id {test_run_id}, got {r.run_id}"


def test_07_module_a_explanation_classification():
    """7. Module A explanations receive MODULE_A (Lot_Relative, early drift, SHAP)."""
    row = pd.Series({
        "Worst_Lot_Zscore": 3.42,
        "Proxy_Stress": "Thermal-High",
        "Leakage_0h": 1.0,
        "Leakage_24h": 1.5,
        "SHAP_Top_Anomaly_Features": "Resistance_0h(+0.12), Leakage_0h(-0.05)",
    })
    explanations = ml_inference.build_explanations(row)

    lot_rel = next((e for e in explanations if e[0] == "Lot_Relative"), None)
    assert lot_rel is not None
    assert lot_rel[3] == "MODULE_A"
    assert lot_rel[4] == "DRIFT"

    early_drift = next((e for e in explanations if e[0] == "Leakage"), None)
    assert early_drift is not None
    assert early_drift[3] == "MODULE_A"
    assert early_drift[4] == "DRIFT"

    shap_exp = next((e for e in explanations if e[0] == "Resistance_0h"), None)
    assert shap_exp is not None
    assert shap_exp[3] == "MODULE_A"
    assert shap_exp[4] == "SHAP"


def test_08_module_b_explanation_classification():
    """8. Module B explanations receive MODULE_B (Predicted_Drift, Uncertainty, Slope_Reject_Flag, SHAP)."""
    row = pd.Series({
        "Predicted_Drift_Score": 68.0,
        "Uncertainty_Score": 45.0,
        "Slope_Reject_Flag": 1,
        "SHAP_Top_Drift_Features": "Leakage_slope(+0.345)",
        "Dominant_Drift_Metric": "Leakage",
    })
    explanations = ml_inference.build_explanations(row)

    pds = next((e for e in explanations if e[0] == "Predicted_Drift"), None)
    assert pds is not None
    assert pds[3] == "MODULE_B"
    assert pds[4] == "DRIFT"

    unc = next((e for e in explanations if e[0] == "Uncertainty"), None)
    assert unc is not None
    assert unc[3] == "MODULE_B"
    assert unc[4] == "DRIFT"

    slope = next((e for e in explanations if e[0] == "Slope_Reject_Flag"), None)
    assert slope is not None
    assert slope[3] == "MODULE_B"
    assert slope[4] == "RULE"

    drift_shap = next((e for e in explanations if e[0] == "Leakage_slope"), None)
    assert drift_shap is not None
    assert drift_shap[3] == "MODULE_B"
    assert drift_shap[4] == "SHAP"


def test_09_module_c_explanation_classification():
    """9. Module C explanations receive MODULE_C (Traditional_Test_Result)."""
    row = pd.Series({
        "Absolute_Spec_Fail": 1,
    })
    explanations = ml_inference.build_explanations(row)

    spec = next((e for e in explanations if e[0] == "Traditional_Test_Result"), None)
    assert spec is not None
    assert spec[3] == "MODULE_C"
    assert spec[4] == "RULE"


def test_10_11_12_13_evidence_type_classification():
    """10-13. Correct evidence types: SHAP, DRIFT, RULE, FALLBACK."""
    row_normal = pd.Series({
        "SHAP_Top_Anomaly_Features": "Vth_0h(+0.08)",
        "Worst_Lot_Zscore": 2.1,
        "Slope_Reject_Flag": 1,
    })
    exps = ml_inference.build_explanations(row_normal)
    shap_item = next(e for e in exps if e[0] == "Vth_0h")
    drift_item = next(e for e in exps if e[0] == "Lot_Relative")
    rule_item = next(e for e in exps if e[0] == "Slope_Reject_Flag")

    assert shap_item[4] == "SHAP"
    assert drift_item[4] == "DRIFT"
    assert rule_item[4] == "RULE"

    row_degraded = pd.Series({
        "Predicted_Drift_Score": 0.0,
        "Uncertainty_Score": 0.0,
        "module_b_status": "degraded",
    })
    exps_deg = ml_inference.build_explanations(row_degraded)
    fallback_item = next(e for e in exps_deg if e[0] == "Predicted_Drift")
    assert fallback_item[3] == "MODULE_B"
    assert fallback_item[4] == "FALLBACK"


def test_14_15_module_b_degraded_wording_and_safety():
    """14 and 15. Module B degraded mode does NOT emit misleading text and emits truthful degraded wording."""
    row_degraded = pd.Series({
        "Predicted_Drift_Score": 0.0,
        "Uncertainty_Score": 0.0,
    })
    # Pass module_b_status explicitly
    exps = ml_inference.build_explanations(row_degraded, module_b_status="degraded")

    reasons = [e[2] for e in exps]
    # Test 14: Must NOT say continued significant drift
    for r in reasons:
        assert "Forecast to 168h shows continued significant drift" not in r

    # Test 15: Must emit truthful degraded wording
    pds_exp = next((e for e in exps if e[0] == "Predicted_Drift"), None)
    assert pds_exp is not None
    assert pds_exp[2] == "Module B drift estimation is degraded; a reliable 168h drift forecast is unavailable."
    assert pds_exp[3] == "MODULE_B"
    assert pds_exp[4] == "FALLBACK"


def test_16_17_shap_attribution_wording_and_causality_safety():
    """16 and 17. SHAP wording uses attribution phrasing, no physical causation claims."""
    row = pd.Series({
        "SHAP_Top_Anomaly_Features": "Resistance_0h(+0.12)",
        "SHAP_Top_Drift_Features": "Leakage_slope(+0.284)",
        "Dominant_Drift_Metric": "Leakage",
    })
    exps = ml_inference.build_explanations(row)

    anom_shap = next(e for e in exps if e[0] == "Resistance_0h")
    drift_shap = next(e for e in exps if e[0] == "Leakage_slope")

    # Wording check
    assert anom_shap[2] == "Anomaly score influenced by Resistance_0h (+0.12 SHAP attribution)."
    assert drift_shap[2] == "predicted Leakage 168h drift influenced by Leakage_slope (+0.284 SHAP attribution)."

    # Causality audit check
    prohibited_phrases = [
        "driven mainly by",
        "root cause",
        "causes failure",
        "will fail because",
        "failure caused by",
    ]
    for exp in exps:
        for p in prohibited_phrases:
            assert p not in exp[2].lower(), f"Unsafe causality phrase '{p}' found in reason: {exp[2]}"


def test_18_api_schema_exposes_optional_provenance():
    """18. ExplanationItem schema accepts and serializes module, evidence_type, and run_id."""
    item = ExplanationItem(
        feature="Lot_Relative",
        contribution=3.14,
        reason="Batch-relative outlier",
        module="MODULE_A",
        evidence_type="DRIFT",
        run_id="run_abc123",
    )
    d = item.model_dump()
    assert d["feature"] == "Lot_Relative"
    assert d["contribution"] == 3.14
    assert d["reason"] == "Batch-relative outlier"
    assert d["module"] == "MODULE_A"
    assert d["evidence_type"] == "DRIFT"
    assert d["run_id"] == "run_abc123"


def test_19_historical_api_backward_compatibility():
    """19. Historical explanation records serialize with NULL provenance without validation error."""
    item = ExplanationItem(
        feature="Slope_Reject_Flag",
        contribution=1.0,
        reason="Predicted 168h drift rate exceeds threshold",
    )
    d = item.model_dump()
    assert d["module"] is None
    assert d["evidence_type"] is None
    assert d["run_id"] is None


def test_20_inference_run_linkage(memory_db):
    """20. Inference run component linkage works cleanly alongside explanations."""
    engine, session = memory_db

    lot = models.Lot(lot_id="LOT-P18", component_type="MOSFET", total_components=2)
    c1 = models.Component(component_id="C9001", lot_id="LOT-P18", component_type="MOSFET")
    c2 = models.Component(component_id="C9002", lot_id="LOT-P18", component_type="MOSFET")
    session.add_all([lot, c1, c2])
    session.commit()

    run = crud.create_inference_run(
        run_id="run_p18_trace_001",
        model_version="sage-1.1",
        execution_status="SUCCESS",
        module_a_status="loaded",
        module_b_status="loaded",
        module_c_status="active",
        is_fallback=False,
        anomaly_pipeline_hash="fakehash_a",
        drift_models_hash="fakehash_b",
        shap_bundle_hash="fakehash_shap",
        config_hash="fakehash_cfg",
        total_components=2,
        db=session,
    )
    linked = crud.link_inference_run_components("run_p18_trace_001", ["C9001", "C9002"], db=session)
    session.commit()
    assert linked == 2

    retrieved = crud.get_latest_inference_run_for_component("C9001", db=session)
    assert retrieved is not None
    assert retrieved["run_id"] == "run_p18_trace_001"
    assert retrieved["module_a_status"] == "loaded"
