from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main


def setup_function():
    test_db = Path(__file__).parent / "test_ledgerlite.db"
    if test_db.exists():
        test_db.unlink()
    main.DB_PATH = test_db
    main.init_db()


def teardown_function():
    if main.DB_PATH.exists():
        main.DB_PATH.unlink()


def client():
    return TestClient(main.app)


def payload(**overrides):
    data = {
        "account_id": 1,
        "amount_cents": 5000,
        "kind": "expense",
        "description": "Tournament registration",
        "actor": "Maya",
    }
    data.update(overrides)
    return data


def headers(key="same-key", role="treasurer"):
    return {"Idempotency-Key": key, "X-Role": role}


def test_retry_changes_balance_only_once():
    c = client()
    first = c.post("/api/transactions", json=payload(), headers=headers())
    second = c.post("/api/transactions", json=payload(), headers=headers())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert c.get("/api/accounts").json()[0]["balance_cents"] == 20000


def test_viewer_cannot_write():
    c = client()
    response = c.post("/api/transactions", json=payload(), headers=headers(role="viewer"))
    assert response.status_code == 403


def test_expense_cannot_overdraw():
    c = client()
    response = c.post(
        "/api/transactions",
        json=payload(amount_cents=999999),
        headers=headers(key="overdraw"),
    )
    assert response.status_code == 409
    assert c.get("/api/accounts").json()[0]["balance_cents"] == 25000


def test_idempotency_key_conflict_is_rejected():
    c = client()
    first = c.post("/api/transactions", json=payload(), headers=headers(key="conflict"))
    second = c.post(
        "/api/transactions",
        json=payload(amount_cents=1000),
        headers=headers(key="conflict"),
    )
    assert first.status_code == 200
    assert second.status_code == 409
