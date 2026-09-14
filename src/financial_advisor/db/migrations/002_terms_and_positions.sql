-- Schema v2 — inputs the M1 observation engine needs that M0 did not collect.
--
-- Applied by db/connection.py with foreign keys OFF and inside one transaction, per
-- SQLite's documented procedure for rebuilding a table; a foreign_key_check runs
-- before commit. A backup of the v1 file is taken first.

-- ---------------------------------------------------------------------------
-- Account terms (F1.6 liability terms, plus deposit yields for F3.2 cash drag).
--
-- One concept for both sides of the ledger: an annual rate on a balance. APY for
-- deposit accounts, APR for debts. Hand-entered — exports and APIs rarely carry it.
-- ---------------------------------------------------------------------------
CREATE TABLE account_terms (
    account_id            INTEGER PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    -- Decimal fraction as text: "0.0425" is 4.25%. A rate, not money — never summed
    -- in SQL, so text keeps it exact.
    rate                  TEXT,
    rate_kind             TEXT CHECK (rate_kind IN ('fixed', 'variable')),
    minimum_payment_cents INTEGER CHECK (minimum_payment_cents IS NULL OR minimum_payment_cents >= 0),
    promo_ends_on         TEXT,
    post_promo_rate       TEXT,
    -- Credit cards only: does the balance carry from month to month? NULL means not
    -- stated. A card paid in full each month accrues no interest, so assuming it
    -- revolves would invent an interest cost — a false finding presented as a true one.
    revolving             INTEGER CHECK (revolving IS NULL OR revolving IN (0, 1)),
    updated_on            TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- Securities: identity only. Metadata (expense ratio, asset-class weights) lives in
-- rules/securities.yml plus the local overlay, read at analysis time. Keeping a copy
-- here too would give the system two sources of truth that drift apart silently.
-- ---------------------------------------------------------------------------
ALTER TABLE security DROP COLUMN expense_ratio;
ALTER TABLE security DROP COLUMN asset_class;

-- ---------------------------------------------------------------------------
-- Positions: rebuilt so quantity may be NULL (cash sweeps and some exports omit it)
-- and so a holdings import carries provenance. A missing market value stays NULL
-- rather than becoming 0 — an unvalued position is reported as unvalued, not as
-- worthless.
-- ---------------------------------------------------------------------------
CREATE TABLE position_v2 (
    id                 INTEGER PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    security_id        INTEGER NOT NULL REFERENCES security(id) ON DELETE CASCADE,
    quantity           TEXT,
    market_value_cents INTEGER,
    as_of_date         TEXT    NOT NULL,
    source             TEXT    NOT NULL DEFAULT 'manual'
                       CHECK (source IN ('manual', 'import', 'sync')),
    import_run_id      INTEGER REFERENCES import_run(id) ON DELETE SET NULL,
    UNIQUE (account_id, security_id, as_of_date)
);

INSERT INTO position_v2 (id, account_id, security_id, quantity, market_value_cents, as_of_date)
     SELECT id, account_id, security_id, quantity, market_value_cents, as_of_date
       FROM position;

DROP TABLE position;
ALTER TABLE position_v2 RENAME TO position;
CREATE INDEX idx_position_account_date ON position (account_id, as_of_date);

INSERT INTO schema_version (version) VALUES (2);
