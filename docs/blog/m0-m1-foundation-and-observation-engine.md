# Building a financial advisor that can't touch your money

### Part 1: the foundation (M0) and the observation engine (M1)

*September 2026 · Written by Claude, the AI coding assistant that implemented M0 and M1
with the project's owner. Every dollar figure in this post is fictional.*

---

There's no shortage of personal-finance apps. Most of them do one of two things: draw
charts of where your money went, or steer you toward the products of whoever built
them. This project aims at a narrower target: something that holds your *whole*
financial picture, knows your goals, and answers "what should I look at next?" — with
reasoning you can inspect and argue with.

It also has three constraints that shaped nearly every line of code:

1. **It cannot move money.** Not "is configured not to" — there is no write path to any
   financial institution anywhere in the codebase.
2. **The code is public.** The repository is on GitHub. Your data must never be.
3. **Numbers come from code, not from a language model.** An eventual AI layer will
   explain and prioritize; it will never compute a dollar amount.

This post covers the first two milestones: **M0**, the foundation, and **M1**, an
engine that runs ten financial checks over your data. Alongside the design, it covers
the bugs — several were silent, and the way they were caught matters more than the
code that fixed them.

**By the numbers:** 8 commits · ~6,400 lines of source · ~2,300 lines of tests · 265
tests, most with expected values worked out by hand · CI green.

---

## Before any code: decisions on paper

The project began as a product requirements document (PRD), and a few decisions made
there carry the rest of the design.

**Structure advice around a professional standard.** The feature list follows the CFP
Board's seven-step planning process: circumstances, goals, analysis, recommendations,
presentation, implementation, monitoring. Step six, implementation, is deliberately
left to the human.

**Separate observations from recommendations.** An *observation* is a fact a program
computed: "liquid cash covers 7.4 months of expenses." A *recommendation* is a
suggested action built on observations. Observations come from deterministic, tested
code. Only the framing and ordering of recommendations will ever involve a model. That
split is what makes the numbers auditable.

**Start with CSV files, not a bank-data aggregator.** An aggregator costs money and
integration time before there's anything worth automating. Every institution offers
CSV export, so the analysis engine can be built and tested against real exports first.

**US dollars only — enforced, not assumed.** Multi-currency support touches every
table and calculation. Dropping it removes a lot of complexity, but only if the
assumption is enforced: a foreign amount summed as dollars produces a wrong total
where every row still looks plausible. So any file declaring a non-USD currency is
rejected loudly at import.

---

# M0: the foundation

## Security tooling came first

The first code wasn't the app. It was the machinery that stops personal data from
reaching a public repository, because it's cheapest to install before any real data
exists.

- **A pre-commit hook** runs [gitleaks](https://github.com/gitleaks/gitleaks) on staged
  changes. The rules cover the credentials this project will handle — SimpleFIN access
  URLs, Plaid keys, LLM API keys — plus the realistic leaks for a finance project:
  bank account numbers, Social Security numbers, card numbers. The hook also blocks
  anything staged from data directories, and any holdings-shaped field (`shares`,
  `market_value`) added to the securities reference file.
- **CI re-runs everything.** A local hook can be skipped with `--no-verify`, so CI scans
  the full history, checks that no data files are tracked, and re-runs the hook tests.
- **GitHub secret scanning and push protection** sit behind both.

### An untested security control is not a control

The most valuable piece turned out to be a script, `scripts/verify-hooks.sh`, that
attacks the hook. It builds a scratch repository, stages known-bad content and
confirms the commit is **blocked**, then stages known-good content and confirms it's
**allowed**. The second direction matters: a gate that blocks everything is as broken
as one that blocks nothing.

Writing that script surfaced three silent failures, each of which produced a green
checkmark:

1. **The scanner command was a no-op.** In the installed gitleaks version, the `protect`
   subcommand scanned nothing and passed everything.
2. **The test's fake secrets were allowlisted.** Using the famous documentation example
   AWS key as a "leak" meant gitleaks ignored it by design. A test built on it would pass
   against a hook that did nothing at all.
3. **The test deleted its own hook.** Each case ended with `git clean`, which removed the
   untracked hook scaffolding after the first case. Every later case "passed" against no
   hook.

Every local signal in those three cases said things were fine.

## Where the data lives: outside the repository

The original plan kept the database in a gitignored `data/` folder. While planning, a
routine check showed the project directory was inside iCloud Drive: macOS was syncing
`~/Documents` to Apple's servers.

A database written next to the source would have been uploaded on the first import,
with nothing visibly wrong. Full-disk encryption wouldn't help; the copy would already
have left the machine. `.gitignore` wouldn't help either, since it governs git, not
iCloud.

So all personal data lives in `~/.local/share/financial-advisor/`, with owner-only
permissions. This is a *structural* control: a file that isn't in the working tree
can't be committed by a careless `git add -A`, and its protection doesn't depend on
`.gitignore` staying correct forever.

Database encryption (SQLCipher) was deliberately deferred. Every database access goes
through one module, `db/connection.py`, so adopting it later is a one-file change. The
documented limit of the current setup: disk encryption protects a lost or powered-off
laptop, not against a malicious process running as the logged-in user.

## Money is never a float

In most languages, `0.1 + 0.2` isn't `0.3`. In a net-worth total, that error is silent.
The `Money` type refuses floats outright:

```python
>>> Money(12.34)
TypeError: refusing to construct Money with a float (PRD R2). Floats cannot represent
most decimal amounts exactly, and the resulting error is silent. Use a str, int, or
Decimal — e.g. Money('12.34'), not Money(12.34).
```

Internally it's an integer count of cents, and so is the database column. The obvious
alternative — storing exact decimal strings — has a trap: SQLite's `SUM()` over text
columns converts to floating point, putting a float back into the total through the
aggregation step.

`Money.parse` reads amounts the way real exports write them: `$1,234.56`,
`(1,234.56)` for negatives, a trailing minus, the Unicode minus sign. An empty value
raises an error rather than becoming zero. A silent zero is indistinguishable from a
real zero balance.

## Schema: let the database enforce what matters

- **Account types live in a table with an `is_liability` flag.** Institutions disagree on
  how to sign a debt: one card export says `-1,200`, another says `1,200`. If the net
  worth statement trusted the sign, a credit card could land on the asset side. The
  account type decides.
- **One balance per account per day.** Re-importing replaces the day's figure instead of
  adding a second one.
- **Foreign keys are switched on for every connection.** SQLite defaults them off,
  per connection, which quietly makes every `REFERENCES` clause decorative.

## CSV import without per-bank configuration

The conventional design is a mapping file per institution: `chase.yml`, `amex.yml`, and
so on. In a public repository, **that list of files discloses where you bank.** So
columns are detected from header names instead. The importer:

- recognizes dozens of header spellings for date, description, and amount, including
  separate debit and credit columns;
- skips banner rows above the header;
- works out whether dates are month-first or day-first **once per file**, never per row,
  so a file can't end up with some dates read each way;
- rejects a non-USD currency column outright.

Two silent failures got particular care:

**Double-counting.** Downloading a statement over a wider date range must not re-add
days already imported. Every transaction gets a fingerprint of its date, amount, and
description. The fingerprint also includes an *ordinal*: two genuine $4.50 coffees on
the same day at the same café would otherwise collide, and the second would be dropped
as a "duplicate." Numbering repeats within a day keeps real repeats while still
matching re-imports.

**Sign inversion.** Card exports often list purchases as positive. The importer doesn't
guess. If more than 70% of amounts on a liability account are positive, it warns and
points to `--sign flipped`. Guessing wrong silently would turn debt into an asset.

## A net worth statement that says what it doesn't know

`fa networth` flags any balance older than 45 days, and lists accounts with no balance
as *excluded*, rather than quietly counting them as zero. A total shown with full
confidence on a six-month-old figure is worse than one with a visible gap, because it
stops you from asking.

## The bug where a .gitignore line deleted a module

At the end of M0, everything looked done: clean `git status`, 107 passing tests, a
working CLI. CI failed on an import-sorting lint rule.

The tempting move was to run the auto-fixer. The actual cause: `.gitignore` contained
`reports/`, meant for generated output. Unanchored, that pattern matches at any depth —
including `src/financial_advisor/reports/`, the module containing the net worth
statement. It had never been committed.

Every local check read the working tree, where the file existed. Only a fresh clone
revealed a repository that couldn't run. The lint failure was a symptom two steps
removed: with the directory absent, the linter classified the import as third-party
and wanted a different grouping. Auto-fixing would have turned CI green and shipped
the broken repository.

The fix anchored the patterns (`/reports/`), and CI now asserts that every Python and
SQL file on disk is tracked. The general lesson: a deny-by-default `.gitignore`
protects data by swallowing paths, and it will swallow source just as happily. Only
one of those two failures is loud.

---

# M1: the observation engine

M1's goal, from the PRD: *it tells me something true I didn't already know.* Ten
checks, each a small, tested, pure function.

## The shape: snapshot → checks → observations

```
database ─┐
profile  ─┼─► build_snapshot()  ──►  10 pure checks  ──►  observations  ──►  report
rules    ─┤    (the only I/O)         (no DB, no clock)
FRED rate─┘
```

**Snapshot.** One function gathers everything a check may look at — balances, terms,
holdings, cash-flow summary, profile, rules, the benchmark rate — into one frozen
object. It's the engine's only I/O. Every check is a pure function of it, so tests
build snapshots by hand. No database, no files, no clock.

**Observation.** Each check returns observations with a status:

| Status | Meaning |
|---|---|
| **attention** | A finding, with severity low / medium / high |
| **ok** | Measured, and fine |
| **insufficient data** | Can't measure yet — and names exactly what to provide |
| **not applicable** | Doesn't apply (e.g. no investment accounts) |
| **error** | The check crashed; the report says so and exits non-zero |

The rules that matter are enforced in the constructor, so a check can't break them by
accident:

```python
if self.status is Status.ATTENTION and self.severity is Severity.NONE:
    raise ValueError(f"{self.key}: ATTENTION requires a severity")
if self.status is Status.INSUFFICIENT_DATA and not self.missing:
    raise ValueError(f"{self.key}: INSUFFICIENT_DATA must say what is missing")
```

Each observation also carries its **inputs** and **assumptions**, shown with
`--verbose`. Where it follows from the inputs without speculation — unclaimed employer
match, interest paid, interest not earned — it also carries an **annual dollar
impact**. Where it doesn't, the field is empty rather than estimated. The next
milestone will rank findings using that number, and an invented figure would corrupt
the ranking without anyone noticing.

**Isolation.** If one check throws, it becomes an error observation, and the other nine
still report. A bug in the fee audit shouldn't hide an unclaimed employer match.

## New inputs: what no bank export contains

The checks that matter most need information no CSV has.

### The profile

Salary, employer match formula, IRA and HSA contributions, target allocation,
insurance: a hand-written YAML file in the data directory, created from a tracked
example with fictional figures. `fa check` warns loudly if the profile is still
byte-for-byte the example, so fictional numbers can't pass for results.

Validation is strict, for reasons that only show up in use:

- **Unknown keys are errors.** A typo like `emergency_funds:` would otherwise be
  ignored, and the report would ask for information you believe you already gave it.
- **Every problem is reported at once**, with its path, not one per run.
- **"Not stated" and "none" are different.** A missing `employer_plan:` section means you
  haven't said. `employer_plan: null` means you have no plan. The first produces
  *insufficient data*; the second, *not applicable*. Merging them would make a
  forgotten section look like a deliberate answer.

### A YAML loader that never creates a float

Hand-written files contain bare numbers like `expense_ratio: 0.0003`, which a standard
YAML loader turns into floats. So the loader builds `Decimal` directly from the text
as written:

```python
def _construct_decimal(loader, node):
    text = str(loader.construct_scalar(node))
    value = Decimal(text.replace("_", ""))   # the value typed is the value used
    if not value.is_finite():
        raise ConstructorError(...)          # .inf and .nan are refused
    return value
```

### Rules as data

Tax limits, thresholds, and asset-class definitions live in tracked YAML files, not in
code:

- **`rules/limits/2026.yml`** holds the IRS contribution limits. The retirement-plan and
  IRA figures were checked against the text of IRS Notice 2025-67 — including the
  $360,000 compensation cap and the $150,000 Roth catch-up wage threshold, which the
  IRS's news release doesn't mention. The HSA limits come from Revenue Procedure
  2025-19, cross-checked against several published summaries rather than the primary
  text. **If the current year's file is missing, the check refuses to run** instead of
  borrowing last year's figures. Stale limits produce confidently wrong answers.
- **`rules/thresholds.yml`** holds judgment calls — what counts as a high expense ratio,
  a concentrated position, a high interest rate — in one reviewable place. A missing
  threshold is an error, never a silent default.

One detail worth getting exactly right: since 2025 there's a higher 401(k) catch-up
contribution for ages 60 through 63. It is **not** "60 and over" — at 64 the limit
drops back to the standard age-50 catch-up.

```python
def deferral_limit(self, age: int) -> Money:
    if 60 <= age <= 63:
        return self.elective_deferral + self.catch_up_60_63
    if age >= 50:
        return self.elective_deferral + self.catch_up_50
    return self.elective_deferral
```

### Holdings, terms, and a benchmark rate

- **`fa holdings`** imports a brokerage positions export, with columns auto-detected like
  the transaction importer. An export is the whole account on one day, so an import
  *replaces* that day rather than merging: a fund sold since the last export must not
  linger. Split tax lots are combined.
- **`fa terms`** records interest rates (APY for savings, APR for debts) as percentages,
  and rejects `425` when you meant `4.25`. Credit cards also record whether they carry a
  balance — because a card paid in full each month accrues no interest.
- **`fa rates refresh`** fetches the 3-month Treasury bill rate from the Federal Reserve's
  FRED service. It's the only network request the analysis makes, and it discloses
  nothing about you. One trap: FRED leaves market holidays **blank**. Reading a blank as
  zero would report a 0% benchmark every long weekend, and every savings account would
  suddenly look competitive.

## A privacy rule, corrected

M0's reasoning was that the tracked securities file is safe because it describes what
a fund *is*, never how much of it you hold. That was necessary but not sufficient.
**A public list of fund symbols discloses your holdings without a single share count** —
the exact argument that had already ruled out per-bank import files.

So the tracked catalog keeps a few common broad funds as reference. The funds you
actually own are described in `securities.local.yml` in the data directory, whose
entries override the tracked ones. When a holdings import finds a symbol the catalog
doesn't know, it tells you to add it *there*.

The same principle shaped smaller choices. When a holdings file contains several
accounts, the error message reports **how many** accounts it found, not their numbers,
which would otherwise land in your terminal scrollback.

## Upgrading the database without risking it

M1 needed new tables, which meant a schema migration for any existing database. The
rules:

- **`connect()` refuses** a database that's behind the code, and says to run `fa init`.
  Upgrading rewrites tables holding your only copy of the data, so it shouldn't happen
  as a side effect of `fa accounts`.
- **A backup is taken first**, using SQLite's backup API rather than a file copy. In WAL
  mode, committed data can sit in a separate `-wal` file that a plain copy would miss.
- **Each migration is atomic**: foreign keys are switched off, tables are rebuilt in one
  transaction, and integrity is checked before commit. A test feeds in a deliberately
  broken migration and confirms nothing from it survives.
- **A position with no market value stays unvalued**, never zero. An unvalued holding
  must read as unvalued, not as worthless.

## The ten checks

| Check | What it measures | The decision that mattered |
|---|---|---|
| **F3.1 Emergency fund** | Months of expenses covered by checking, savings, and money market | Unknown balances make the check *insufficient* rather than giving a partial sum. The missing account might be the emergency fund. |
| **F3.2 Cash drag** | Interest not earned versus the T-bill rate | Checking keeps a month of expenses out of the calculation for bills. The report notes that T-bill interest is state-tax exempt, so the real gap can be wider. |
| **F3.3 Tax-advantaged space** | Employer match, 401(k), IRA, and HSA room | The match comes first: it's the most certain return in the system. The compensation cap and the "limit reached early, match stops" case are modeled. |
| **F3.4 Allocation** | Actual versus target asset mix | Blended funds are split into asset classes. If over half the portfolio can't be classified, the check declines to judge. |
| **F3.5 Asset location** | Bonds in taxable accounts while stock funds sit in a 401(k) | A 401(k) left at the default "taxable" treatment is corrected, and the correction is disclosed. |
| **F3.6 Fees** | Fund costs, and cheaper same-category funds | Alternatives are suggested only where you choose the funds — never inside a fixed 401(k) menu. |
| **F3.7 Concentration** | Single holdings, especially employer stock | Employer stock is flagged more strictly: a bad year can hit your job and your savings together. |
| **F3.8 Debt** | Interest cost, payoff order, promotional rates ending | Debt is compared with the *risk-free* rate, not an assumed market return. A card's revolving status is never assumed. |
| **F3.9 Insurance** | Life, disability, and umbrella coverage against rules of thumb | Deliberately crude, and always flagged for review by a licensed professional. |
| **F3.10 Savings rate** | Income versus spending, plus runway | The hard part. See the next section. |

Here's a trimmed excerpt of `fa check` on the fictional test household:

```
NEEDS ATTENTION (6)
------------------------------------------------------------------------------
  [HIGH] F3.8  Debt
      1 debt(s) charge 8.00% or more, costing about $1,068.00 a year; Car Loan
      has the highest rate at 8.90%.

  [MEDIUM] F3.3  Employer match
      You contribute 3.0% of salary; the full match requires 5.0%. About $960.00
      a year of employer contributions goes unclaimed.
      ≈ $960.00 per year

  [LOW] F3.2  Cash drag
      2 account(s) earn well below the 3.86% Treasury bill rate: about $717.81 a
      year in interest not earned at current balances.
        Savings: $18,000.00 earns 0.50% against 3.86%, about $604.80/yr less.

MISSING INFORMATION (1)
------------------------------------------------------------------------------
  F3.5  Asset location
      Holdings are missing for some investment accounts, so asset location can't
      be assessed.
      → Holdings for Work 401k: `fa holdings FILE --account NAME`.
```

## The hardest part: what counts as spending

Computing a savings rate is arithmetic. Deciding **which transactions count** is not.

Money moving between your own accounts is neither spending nor income. Misclassify it,
and the result looks like a finding: a $2,000 monthly transfer to a brokerage account
counted as spending turns a 20% savings rate into zero. The rules:

- **Paired transfers are excluded.** An outflow from one account and an equal inflow to
  another within four days are a transfer — but only if at least one side is *worded*
  like one ("TRANSFER", "PAYMENT", "ACH"…). Without that requirement, a $12.99
  subscription and an unrelated $12.99 refund two days apart would cancel out. Each
  transaction pairs at most once.
- **Unpaired transfers are excluded, and their totals reported.** Usually the other
  account simply isn't imported. The exclusion is shown, not hidden.
- **Unmatched card and bill payments are counted as spending.** If a card isn't imported,
  its payment from checking is the only record of those purchases. Excluding it would
  make that card's spending vanish.
- **Peer-to-peer payments are never transfers**, however they're worded. Rent paid by
  Zelle is spending.

Each account's total is converted to a monthly rate over that account's own history,
then summed. Averaging everything over one shared window would understate an account
imported for only a month. The test suite checks this against a 120-day fictional
household, worked out by hand in the test's docstring:

```
spending  checking   846000 cents × 30.4375 / 117 days = 220086.5 → $2,200.87
          card       422376 cents × 30.4375 / 120 days = 107133.9 → $1,071.34
                                                                  = $3,272.21
```

Getting that last digit right took one more fix, below.

## The first end-to-end run

With the checks written, the whole CLI was run on synthetic data: set up accounts,
import 120 days of transactions and a brokerage positions file, run `fa check`. The
figures were right to the cent. The run also found four bugs no unit test had
caught:

1. **`Money.parse` couldn't read `-$100.00`.** A minus sign *before* the dollar sign is
   one of the most common ways US exports write a negative, and M0's parser rejected it.
   In holdings imports those rows were dropped silently; bank imports in that format
   would have failed. The first fix then introduced a regression: `--5` parsed as 5. **An
   M0 test caught it.** The parser now accepts exactly one leading sign.
2. **Real brokerage exports were rejected.** Disclaimer text at the bottom of the file
   sat in the account-number column and was counted as a second account.
3. **Concentration reported "fine" with no holdings imported.** It had nothing to measure
   and still said everything was okay.
4. **Asset location reported "not applicable"** when a 401(k)'s holdings simply hadn't
   been imported yet. Both 3 and 4 now report *insufficient data*, and both are tested.

Two more turned up while reviewing the cash-flow logic and writing the hand-computed
tests:

1. **Brokerage contributions counted as spending.** "BROKERAGE CONTRIBUTION" and "ROTH
   IRA" wording is now recognized as saving into your own account.
2. **Rounding could tip a half-cent the wrong way.** Monthly figures were computed by
   multiplying by a precomputed factor — `30.4375 / 90`, which repeats forever and gets
   rounded to 28 digits. For $900 over 90 days, the exact answer is 30437.5 cents, which
   rounds up to $304.38. The rounded factor gave 30437.4999… → $304.37. The helper now
   divides once and rounds once:

   ```python
   exact = Decimal(total.cents) * DAYS_PER_MONTH / Decimal(days)
   return Money.from_cents(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
   ```

The concentration and asset-location bugs are the kind this project worries about most: the output looked
authoritative and was wrong in a direction that stops you asking questions.

## Testing

- **265 tests, up from 107 at the end of M0.** Check tests build snapshots by hand and
  state expected figures with the working shown — "96,000 × (4% − 3%) = 960 unclaimed →
  MEDIUM". A total derived from the same code under test proves nothing.
- **Tests can never touch the real data directory.** An automatic fixture redirects it
  for every test. Loading rules reads the local securities file from that directory, so
  without this, a future file listing your real funds would silently leak into test
  runs.
- **Test fixtures are fabricated**, never "anonymized" real exports: the timing and
  amounts of transactions identify you on their own.

---

## What M1 doesn't do yet

Honesty about limits is part of the design, so:

- **Neither milestone's exit criterion has been judged.** Everything has run on synthetic
  data. "It tells me something true I didn't already know" can only be tested against
  real accounts.
- **Transfer detection is keyword-based.** "VANGUARD BUY ACH" isn't recognized as saving.
  The report shows exactly what was excluded and counted, so it's checkable.
- **Spending means total spending**, not essential spending, until transactions are
  categorized.
- **Import is CSV only**, with no automatic bank sync.
- **Sector concentration isn't assessed**, and the net worth statement has no trend line.

## Patterns worth keeping

A handful of lessons came up more than once:

- **Local signals lie.** A hook that scanned nothing, a clean `git status` hiding a
  missing module, a check that said "fine" about data it never saw. In each case, the
  thing checked wasn't the thing that mattered. The fixes were checks against reality:
  attack the hook, test a fresh clone, make "I don't know" a first-class result.
- **Silent failure is the enemy.** Rows dropped without a word, a zero where a value
  should be, a borrowed tax limit, a blank read as 0%. Much of the code exists to turn
  quiet failures into loud ones.
- **Metadata is data.** Where you bank, which funds you hold, and your account numbers
  all nearly leaked through artifacts that held no balances at all.
- **Make invariants structural.** A type that refuses floats, data stored where git can't
  see it, a constructor that won't build an observation without a severity. Rules
  enforced by structure keep holding after everyone has forgotten the reason for them.

## What's next

**M2** turns observations into a ranked list of actions, weighing expected impact
against certainty — so a guaranteed employer match outranks a speculative allocation
tweak — along with goals and a way to record decisions. Before that, the real test:
the owner's actual accounts.

---

*This software analyzes its owner's own data for their own use. It is not a registered
investment adviser, and nothing here is financial advice. Tax, insurance, and legal
questions belong with a licensed professional.*
