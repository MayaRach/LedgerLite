from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ledgerlite.db"

app = FastAPI(title="LedgerLite", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")


class TransactionIn(BaseModel):
    account_id: int
    amount_cents: int = Field(gt=0)
    kind: Literal["deposit", "expense"]
    description: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=100)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                balance_cents INTEGER NOT NULL DEFAULT 0 CHECK(balance_cents >= 0)
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                kind TEXT NOT NULL CHECK(kind IN ('deposit', 'expense')),
                description TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                transaction_id INTEGER NOT NULL REFERENCES transactions(id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                account_id INTEGER NOT NULL,
                transaction_id INTEGER,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO accounts(id, name, balance_cents) VALUES (1, 'Robotics Club', 25000)")
        conn.execute("INSERT OR IGNORE INTO accounts(id, name, balance_cents) VALUES (2, 'Debate Club', 15000)")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/accounts")
def list_accounts():
    with connect() as conn:
        rows = conn.execute("SELECT id, name, balance_cents FROM accounts ORDER BY id").fetchall()
        return [dict(row) for row in rows]


@app.get("/api/transactions")
def list_transactions(limit: int = 50):
    limit = max(1, min(limit, 200))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.account_id, a.name AS account_name, t.amount_cents,
                   t.kind, t.description, t.actor, t.created_at
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def canonical_hash(tx: TransactionIn) -> str:
    body = json.dumps(tx.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def require_write_role(role: str | None) -> None:
    if role not in {"treasurer", "admin"}:
        raise HTTPException(status_code=403, detail="treasurer or admin role required")


@app.post("/api/transactions")
def create_transaction(
    tx: TransactionIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    role: str | None = Header(default=None, alias="X-Role"),
):
    require_write_role(role)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    key = idempotency_key.strip()
    request_hash = canonical_hash(tx)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT request_hash, transaction_id FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                conn.execute("ROLLBACK")
                raise HTTPException(status_code=409, detail="idempotency key reused with different request")
            row = conn.execute("SELECT * FROM transactions WHERE id = ?", (prior["transaction_id"],)).fetchone()
            conn.execute("COMMIT")
            return {**dict(row), "replayed": True}

        account = conn.execute(
            "SELECT id, name, balance_cents FROM accounts WHERE id = ?", (tx.account_id,)
        ).fetchone()
        if not account:
            conn.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="account not found")

        current = account["balance_cents"]
        new_balance = current + tx.amount_cents if tx.kind == "deposit" else current - tx.amount_cents
        if new_balance < 0:
            conn.execute("ROLLBACK")
            raise HTTPException(status_code=409, detail="expense would overdraw account")

        cur = conn.execute(
            "INSERT INTO transactions(account_id, amount_cents, kind, description, actor) VALUES (?, ?, ?, ?, ?)",
            (tx.account_id, tx.amount_cents, tx.kind, tx.description, tx.actor),
        )
        transaction_id = cur.lastrowid
        conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = ?", (new_balance, tx.account_id))
        conn.execute(
            "INSERT INTO idempotency_keys(key, request_hash, transaction_id) VALUES (?, ?, ?)",
            (key, request_hash, transaction_id),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, account_id, transaction_id, detail) VALUES (?, ?, ?, ?, ?)",
            (tx.actor, tx.kind, tx.account_id, transaction_id,
             f"{tx.kind} {tx.amount_cents} cents; balance {current} -> {new_balance}"),
        )
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        conn.execute("COMMIT")
        return {**dict(row), "replayed": False, "balance_cents": new_balance}
    except HTTPException:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


@app.get("/api/audit")
def audit_log(role: str | None = Header(default=None, alias="X-Role")):
    if role not in {"treasurer", "admin"}:
        raise HTTPException(status_code=403, detail="treasurer or admin role required")
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]
