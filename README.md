# LedgerLite

LedgerLite is a small treasury system for school clubs. I built it to explore a deceptively hard question: **how do you make a money-like operation safe when requests fail, retry, or arrive from users with different permissions?**

Instead of focusing on flashy features, the project focuses on correctness and reliability.

## What it does

- Tracks balances for multiple school clubs
- Records deposits and expenses
- Prevents overdrafts
- Uses role-based authorization for balance-changing actions
- Uses idempotency keys so retries do not create duplicate transactions
- Stores an immutable transaction history and audit trail
- Exposes both a browser UI and JSON API
- Includes automated tests for the most important failure cases

## Why idempotency matters

A user can click “submit” and then lose Wi-Fi before seeing the response. Their browser may retry the request. Without protection, the same expense could be recorded twice.

LedgerLite requires an `Idempotency-Key` for API writes. The server stores the key together with a hash of the original request. If the exact request is retried, the original transaction is returned rather than creating a second one. If the same key is reused for a different request, the server rejects it with `409 Conflict`.

## Architecture

```text
Browser / API Client
        |
        v
    FastAPI
        |
   validation + role checks
        |
        v
SQLite transaction (BEGIN IMMEDIATE)
   |        |          |
accounts  ledger   idempotency keys
              \
               -> audit log
```

## Hardest technical decision

The hardest part was deciding how to prevent a transaction from partially succeeding. Updating a balance, inserting the ledger entry, storing the idempotency key, and writing the audit event need to behave like one operation. I used a database transaction so either all of those changes commit or none of them do.

I also used `BEGIN IMMEDIATE` in SQLite so two simultaneous writers cannot both read the same balance and independently spend it.

## Testing strategy

The tests target failure modes rather than only the happy path:

1. retrying the same request changes the balance only once
2. a viewer cannot mutate balances
3. an expense cannot overdraw an account
4. reusing an idempotency key with different request data is rejected

Run them with:

```bash
pytest -q
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000`.

Interactive API docs are at `http://127.0.0.1:8000/docs`.

## Example API request

```bash
curl -X POST http://127.0.0.1:8000/api/transactions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: tournament-fee-001' \
  -H 'X-Role: treasurer' \
  -d '{
    "account_id": 1,
    "amount_cents": 4500,
    "kind": "expense",
    "description": "Tournament registration",
    "actor": "Maya"
  }'
```

## Tradeoffs and what I would improve

SQLite keeps the project easy to run and lets me demonstrate transaction semantics without infrastructure overhead. For a production deployment, I would switch to PostgreSQL, use real authentication rather than a demo role header, add row-level locking where appropriate, and move authorization into a richer user/membership model.

I would also add double-entry accounting rather than storing only a running club balance. That would make the financial model easier to audit and extend.

## What I learned

The main lesson was that “record an expense” is not really one operation. A reliable system has to think about retries, concurrency, authorization, invariant enforcement, and observability together. The code for the happy path is small; the interesting engineering is deciding what should happen when something goes wrong.
