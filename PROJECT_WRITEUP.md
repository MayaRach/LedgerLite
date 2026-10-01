# Application write-up

**Problem:** School clubs often track money in spreadsheets, where duplicate entries, accidental overdrafts, and unclear edit history are easy to create. I built LedgerLite, a small treasury system that treats club transactions as reliability-sensitive operations.

**My contribution:** I designed and implemented the full project in Python with FastAPI and SQLite, including the transaction API, browser interface, authorization checks, idempotency mechanism, audit log, database schema, and automated tests.

**Hardest technical decision:** I wanted a retry after a network failure to be safe. Each write requires an idempotency key. I store that key with a hash of the request, return the original transaction for an exact retry, and reject reuse with different data. I also wrap the balance update, ledger insert, idempotency record, and audit event in a single database transaction so they cannot partially succeed.

**Testing:** I wrote automated tests around the failure cases I cared about most: duplicate retries, unauthorized writes, overdrafts, and conflicting reuse of an idempotency key.

**What I learned:** The interesting part of a financial system is not the happy path. Reliability comes from defining invariants and deciding what should happen during retries, concurrency, invalid input, and permission failures. If I continued the project, I would move to PostgreSQL, add real authentication, and use double-entry accounting.
