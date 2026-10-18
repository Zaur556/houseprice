import json

from analytics import export
from scraper import db
from tools import seed_demo


def test_publish_keeps_newest(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 'e.db'}")
    assert db.latest_export(engine) is None
    for i in range(db.KEEP_EXPORTS + 3):
        db.publish_export(engine, json.dumps({"n": i}))
    assert json.loads(db.latest_export(engine)) == {"n": db.KEEP_EXPORTS + 2}
    with engine.connect() as c:
        assert len(c.execute(db.exports.select()).fetchall()) == db.KEEP_EXPORTS


def test_export_end_to_end(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    get_engine = db.get_engine
    monkeypatch.setattr(db, "get_engine", lambda *a, **k: get_engine(url))
    monkeypatch.setattr(export, "DASHBOARD_DATA", tmp_path / "data.json")
    seed_demo.main(n=1500)
    export.main([])

    data = json.loads(db.latest_export(db.get_engine()))
    assert data == json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    s = data["segments"]["sale|all|all"]
    assert s["kpi"]["active"] > 0
    assert len(s["change_trend"]) == len(data["meta"]["weeks"])
    assert sum(w["cuts"] for w in s["change_trend"]) > 0
    assert any(d.get("cut_share_30d") for d in s["districts"])
