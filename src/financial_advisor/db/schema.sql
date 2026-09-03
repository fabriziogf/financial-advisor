-- Schema v1 — M0 foundation.
--
-- Money is stored as INTEGER cents throughout. SQLite has no decimal type, and
-- storing amounts as TEXT would make SUM() coerce to float, putting a float back
-- into the net worth path through the back door. Integer minor units keep SQL
-- aggregation exact; the Decimal-facing API lives in money.py (PRD Appendix A.5).
--
-- Every amount is USD (PRD D8 / §12.1). The CHECK constraints below document that
-- invariant in the one place a future reader is guaranteed to look.

PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (
    version     INTEGER NOT NULL PRIMARY KEY,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Reference: account taxonomy. A lookup table rather than a string convention so
-- the asset/liability split is enforced by the database. Getting a mortgage
-- counted as an asset is a whole-number-of-hundreds-of-thousands error, and it is
-- exactly the kind that looks fine on a dashboard.
CREATE TABLE account_type (
    code            TEXT    PRIMARY KEY,
    label           TEXT    NOT NULL,
    is_liability    INTEGER NOT NULL CHECK (is_liability IN (0, 1)),
    is_investment   INTEGER NOT NULL DEFAULT 0 CHECK (is_investment IN (0, 1)),
    sort_order      INTEGER NOT NULL DEFAULT 100
);

INSERT INTO account_type (code, label, is_liability, is_investment, sort_order) VALUES
    ('checking',        'Checking',              0, 0, 10),
    ('savings',         'Savings',               0, 0, 20),
    ('money_market',    'Money Market',          0, 0, 30),
    ('cd',              'Certificate of Deposit',0, 0, 40),
    ('brokerage',       'Taxable Brokerage',     0, 1, 50),
    ('retirement_401k', '401(k)',                0, 1, 60),
    ('ira_traditional', 'Traditional IRA',       0, 1, 70),
    ('ira_roth',        'Roth IRA',              0, 1, 80),
    ('hsa',             'HSA',                   0, 1, 90),
    ('529',             '529 Plan',              0, 1, 100),
    ('crypto',          'Crypto',                0, 1, 110),
    ('real_estate',     'Real Estate',           0, 0, 120),
    ('vehicle',         'Vehicle',               0, 0, 130),
    ('other_asset',     'Other Asset',           0, 0, 140),
    ('credit_card',     'Credit Card',           1, 0, 200),
    ('mortgage',        'Mortgage',              1, 0, 210),
    ('auto_loan',       'Auto Loan',             1, 0, 220),
    ('student_loan',    'Student Loan',          1, 0, 230),
    ('personal_loan',   'Personal Loan',         1, 0, 240),
    ('other_liability', 'Other Liability',       1, 0, 250);

CREATE TABLE institution (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    slug        TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE account (
    id              INTEGER PRIMARY KEY,
    institution_id  INTEGER REFERENCES institution(id) ON DELETE SET NULL,
    name            TEXT    NOT NULL,
    type_code       TEXT    NOT NULL REFERENCES account_type(code),
    -- 'taxable' | 'tax_deferred' | 'tax_free'. Drives asset-location analysis in M1.
    tax_treatment   TEXT    NOT NULL DEFAULT 'taxable'
                    CHECK (tax_treatment IN ('taxable', 'tax_deferred', 'tax_free')),
    currency        TEXT    NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- Declared assets (F1.2): property, private holdings, vehicles. No separate
    -- concept needed — they are accounts whose balances are entered by hand.
    is_manual       INTEGER NOT NULL DEFAULT 0 CHECK (is_manual IN (0, 1)),
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (institution_id, name)
);

CREATE TABLE import_run (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER REFERENCES account(id) ON DELETE CASCADE,
    source_name     TEXT,
    -- Idempotency: the same file imported twice is a no-op, not a double-count.
    file_sha256     TEXT    NOT NULL,
    row_count       INTEGER NOT NULL DEFAULT 0,
    inserted_count  INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL CHECK (status IN ('ok', 'failed')),
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, file_sha256)
);

CREATE TABLE balance_snapshot (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    as_of_date    TEXT    NOT NULL,          -- ISO-8601 date
    amount_cents  INTEGER NOT NULL,          -- signed, natural value
    source        TEXT    NOT NULL DEFAULT 'manual'
                  CHECK (source IN ('manual', 'import', 'sync')),
    import_run_id INTEGER REFERENCES import_run(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    -- One balance per account per day; a re-import replaces rather than appends.
    UNIQUE (account_id, as_of_date)
);

CREATE TABLE txn (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    posted_on       TEXT    NOT NULL,        -- ISO-8601 date
    amount_cents    INTEGER NOT NULL,        -- signed: negative = money out
    description     TEXT    NOT NULL,        -- normalized
    raw_description TEXT,                    -- exactly as the file had it
    -- Dedupe across overlapping exports. Re-downloading a statement with a wider
    -- date range must not double-count the days already imported — the failure
    -- most likely to corrupt a total while every row still looks plausible.
    dedupe_hash     TEXT    NOT NULL,
    import_run_id   INTEGER REFERENCES import_run(id) ON DELETE SET NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, dedupe_hash)
);

CREATE INDEX idx_txn_account_date ON txn (account_id, posted_on);
CREATE INDEX idx_balance_account_date ON balance_snapshot (account_id, as_of_date);

-- Reference data about instruments, loaded from rules/securities.yml. Describes
-- what a security IS; never that any is held. Holdings live in `position`.
CREATE TABLE security (
    id             INTEGER PRIMARY KEY,
    symbol         TEXT    NOT NULL UNIQUE,
    name           TEXT,
    -- Decimal-as-text: a rate, not money, and never summed in SQL.
    expense_ratio  TEXT,
    -- JSON object of asset-class weights, e.g. {"us_equity": 0.6, "intl_equity": 0.4}.
    -- Fractional weights rather than a single label, so a target-date fund can be
    -- decomposed — without look-through, allocation analysis reports one
    -- unclassified position and produces nothing useful (PRD §9.1).
    asset_class    TEXT,
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE position (
    id                 INTEGER PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    security_id        INTEGER NOT NULL REFERENCES security(id) ON DELETE CASCADE,
    -- A share count, NOT money: fractional shares are normal, and this is never
    -- summed across securities. Decimal-as-text is correct here.
    quantity           TEXT    NOT NULL,
    market_value_cents INTEGER,
    as_of_date         TEXT    NOT NULL,
    UNIQUE (account_id, security_id, as_of_date)
);

INSERT INTO schema_version (version) VALUES (1);
