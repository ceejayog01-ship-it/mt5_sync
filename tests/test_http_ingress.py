from fastapi.testclient import TestClient
from signal_interface.exporter import _demo_signals
import mt5_receiver.http_api as api


def test_authenticated_delivery_is_durable_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "TOKEN", "test-secret")
    monkeypatch.setattr(api, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "db" / "inbox.sqlite3")
    monkeypatch.setattr(api, "MT5_FILES_DIR", tmp_path / "mt5-files")
    client = TestClient(api.app)
    signals = [s.model_dump(mode="json") for s in _demo_signals(2)]

    denied = client.post("/v1/signals", json={"signals": signals})
    assert denied.status_code == 401

    headers = {"Authorization": "Bearer test-secret"}
    first = client.post("/v1/signals", json={"batch_id": "test-1", "signals": signals}, headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert len(body["accepted"]) == 2
    assert body["durable_inbox_count"] == 2

    second = client.post("/v1/signals", json={"batch_id": "test-1", "signals": signals}, headers=headers)
    assert second.status_code == 200
    assert len(second.json()["duplicates"]) == 2
    assert second.json()["durable_inbox_count"] == 2

    csv_path = tmp_path / "mt5-files" / "tse_signals.csv"
    rows = csv_path.read_text().splitlines()
    assert len(rows) == 3  # header + two durable signals
    assert rows[0].startswith("signal_id,instrument,direction")


def test_empty_batch_does_not_create_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "TOKEN", "test-secret")
    monkeypatch.setattr(api, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "db" / "inbox.sqlite3")
    monkeypatch.setattr(api, "MT5_FILES_DIR", tmp_path / "mt5-files")
    client = TestClient(api.app)
    response = client.post("/v1/signals", json={"signals": []}, headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert response.json()["durable_inbox_count"] == 0
