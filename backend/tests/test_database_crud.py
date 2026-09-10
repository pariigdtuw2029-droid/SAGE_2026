"""Tests for Member 2's CRUD + query layer (database/crud.py)."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import crud
from database.connection import Base


@pytest.fixture()
def db():
    """Fresh in-memory SQLite session (shared connection via StaticPool)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)


def _seed(db):
    crud.create_lot("LOT-1", component_type="MOSFET", temperature=25.0,
                    total_components=2, db=db)
    crud.create_component("C101", "LOT-1", "MOSFET", status="PASS", db=db)
    crud.create_component("C102", "LOT-1", "MOSFET", status="REJECT", db=db)
    crud.add_measurements([
        {"component_id": "C101", "timestamp": "2025-01-06 00:00:00",
         "parameter": "Leakage", "value": 1.0, "temperature": 25.0, "checkpoint_hours": 0},
        {"component_id": "C101", "timestamp": "2025-01-07 00:00:00",
         "parameter": "Leakage", "value": 1.5, "temperature": 25.0, "checkpoint_hours": 24},
    ], db=db)
    crud.add_prediction("C101", "Leakage", predicted_168h=2.0, actual_168h=1.9, db=db)
    crud.add_risk_assessment("C101", anomaly_score=10.0, drift_score=5.0,
                             risk_score=12.0, decision="PASS", confidence=0.9, db=db)
    crud.add_risk_assessment("C102", anomaly_score=85.0, drift_score=70.0,
                             risk_score=88.0, decision="REJECT", confidence=0.4, db=db)
    crud.add_explanations("C101", [("Leakage", 50.0, "Leakage moved +50% in 24h.")], db=db)

    # Mirror ingestion step 6: refresh cached snapshots from the risk rows.
    crud.update_component_snapshot("C101", db=db, status="PASS", decision="PASS",
                                   risk_level="LOW", anomaly_score=10.0, drift_score=5.0,
                                   risk_score=12.0, confidence=0.9)
    crud.update_component_snapshot("C102", db=db, status="REJECT", decision="REJECT",
                                   risk_level="CRITICAL", anomaly_score=85.0, drift_score=70.0,
                                   risk_score=88.0, confidence=0.4)
    db.commit()


def test_insert_and_get_lot(db):
    _seed(db)
    lot = crud.get_lot("LOT-1", db=db)
    assert lot["lot_id"] == "LOT-1"
    assert lot["total_components"] == 2


def test_list_lots(db):
    _seed(db)
    lots = crud.list_lots(db=db)
    assert len(lots) == 1
    assert lots[0]["lot_id"] == "LOT-1"


def test_get_lot_summary(db):
    _seed(db)
    summary = crud.get_lot_summary("LOT-1", db=db)
    assert summary["total_components"] == 2
    assert summary["passed"] == 1
    assert summary["reject"] == 1
    assert summary["average_anomaly_score"] == pytest.approx(47.5)
    assert summary["high_risk_components"] == 1  # C102 risk 88 ≥ 60


def test_get_component(db):
    _seed(db)
    comp = crud.get_component("C101", db=db)
    assert comp["component_id"] == "C101"
    assert comp["lot_id"] == "LOT-1"
    assert comp["decision"] == "PASS"
    assert comp["predicted_168h"] == 2.0


def test_get_component_measurements(db):
    _seed(db)
    rows = crud.get_component_measurements("C101", db=db)
    assert len(rows) == 2
    assert rows[0]["parameter"] == "Leakage"

    only = crud.get_component_measurements("C101", parameter="Leakage", db=db)
    assert len(only) == 2


def test_get_component_prediction(db):
    _seed(db)
    pred = crud.get_component_prediction("C101", db=db)
    assert pred["predicted_168h"] == 2.0
    assert pred["actual_168h"] == 1.9
    assert pred["prediction_error"] == pytest.approx(-0.1)


def test_get_component_risk(db):
    _seed(db)
    risk = crud.get_component_risk("C102", db=db)
    assert risk["risk_score"] == 88.0
    assert risk["decision"] == "REJECT"


def test_get_alerts(db):
    _seed(db)
    alerts = crud.get_alerts(db=db)
    assert len(alerts) == 1
    assert alerts[0]["component_id"] == "C102"
    assert alerts[0]["severity"] == "CRITICAL"  # 88 ≥ 80


def test_get_component_trajectory(db):
    _seed(db)
    traj = crud.get_component_trajectory("C101", db=db)
    assert traj["parameter"] == "Leakage"
    assert len(traj["actual"]) == 2
    assert traj["predicted"][0]["time"] == 168
    assert traj["safety_limit"] == 3.0  # leakage datasheet bound


def test_get_component_report(db):
    _seed(db)
    report = crud.get_component_report("C101", db=db)
    assert report["component_id"] == "C101"
    assert report["decision"] == "PASS"
    assert len(report["reasons"]) == 1
    assert "Leakage" in report["reasons"][0]
    assert report["measurement_summary"]["Leakage"]["drift_pct"] == pytest.approx(50.0)
    assert report["prediction"]["predicted_168h"] == 2.0
    assert report["risk_assessment"]["risk_score"] == 12.0


def test_update_component_snapshot(db):
    _seed(db)
    ok = crud.update_component_snapshot("C101", db=db, risk_score=42.0, decision="MONITOR")
    assert ok
    comp = crud.get_component("C101", db=db)
    assert comp["risk_score"] == 42.0
    assert comp["decision"] == "MONITOR"


def test_delete_component_and_lot(db):
    _seed(db)
    assert crud.delete_component("C101", db=db)
    assert crud.get_component("C101", db=db) is None
    assert crud.delete_lot("LOT-1", db=db)
    assert crud.get_lot("LOT-1", db=db) is None


def test_missing_rows_return_none(db):
    _seed(db)
    assert crud.get_lot("NOPE", db=db) is None
    assert crud.get_component("NOPE", db=db) is None
    assert crud.get_lot_summary("NOPE", db=db) is None
    assert crud.get_component_report("NOPE", db=db) is None