"""End-to-end ingestion test (database/ingestion.py) on real data."""

import os

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import crud, ingestion
from database.connection import Base
from database.validation import DataValidationError

DATA_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "burn_in_dataset.csv")


@pytest.fixture()
def db():
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


def test_ingest_small_slice(db):
    if not os.path.exists(DATA_CSV):
        pytest.skip("burn_in_dataset.csv not present")
    df = pd.read_csv(DATA_CSV).head(60)
    result = ingestion.ingest_dataframe(df, db=db, filename="burn_in_dataset.csv")
    assert result.ok
    assert result.components_created > 0
    assert result.predictions_created > 0
    assert result.risk_assessments_created == result.components_created
    assert result.explanations_created >= result.components_created

    # Database now holds real rows.
    counts = crud.count_rows(db=db)
    assert counts["lots"] >= 1
    assert counts["components"] == result.components_created
    assert counts["measurements"] == result.components_created * 12

    # Every stored component has risk + prediction + explanation.
    lots = crud.list_lots(db=db)
    assert lots
    summary = crud.get_lot_summary(lots[0]["lot_id"], db=db)
    assert summary["total_components"] > 0
    assert (summary["passed"] + summary["monitor"] + summary["hold"] + summary["reject"]
            == summary["total_components"])

    comps = [c for c in _all_components(db)]
    cid = comps[0]
    report = crud.get_component_report(cid, db=db)
    assert report["component_id"] == cid
    assert report["decision"] in {"PASS", "MONITOR", "HOLD", "REJECT"}
    assert report["risk_score"] is not None
    assert report["confidence"] is not None
    assert len(report["explanations"]) > 0
    traj = crud.get_component_trajectory(cid, db=db)
    assert len(traj["actual"]) == 4  # 0h/24h/96h/168h
    assert traj["predicted"][0]["time"] == 168

    # Alerts must be consistent with risk scores.
    alerts = crud.get_alerts(db=db)
    for a in alerts:
        assert a["risk_score"] >= 50


def test_reingest_is_idempotent(db):
    if not os.path.exists(DATA_CSV):
        pytest.skip("burn_in_dataset.csv not present")
    df = pd.read_csv(DATA_CSV).head(30)
    r1 = ingestion.ingest_dataframe(df, db=db)
    r2 = ingestion.ingest_dataframe(df, db=db)
    counts = crud.count_rows(db=db)
    assert counts["components"] == r1.components_created
    assert counts["predictions"] == r1.predictions_created
    assert counts["risk_assessments"] == r1.risk_assessments_created
    assert counts["explanations"] == r1.explanations_created
    assert r2.components_created == r1.components_created


def test_wrong_columns_raises_data_validation_error(db):
    bad = pd.DataFrame({"A": [1], "B": [2]})
    with pytest.raises(DataValidationError):
        ingestion.ingest_dataframe(bad, db=db)


def _all_components(db):
    from sqlalchemy import select
    from database import models
    return list(db.scalars(select(models.Component.component_id)).all())