# PRD — Personal Financial Advisor

| | |
|---|---|
| **Status** | v0.4 — M0 and M1 built; exit criteria await real data; M2 next |
| **Author** | fabriziogf |
| **Last updated** | 2026-09-13 |
| **Build log** | [M0 and M1: how they were built](blog/m0-m1-foundation-and-observation-engine.md) |
| **Repo** | Public. See [SECURITY.md](../SECURITY.md). |

> **How to read this.** §12 records the settled decisions, including those made while
> building M0 and M1 (D9–D15). §15 tracks what's implemented, what isn't, and known
> limitations. Options that were considered and rejected are preserved in
> [Appendix A](#appendix-a--alternatives-considered), each with the reasoning and a
> trigger for revisiting it, so a future reversal starts from the argument rather than
> from scratch.

---

## 1. Problem

The data needed to make informed money decisions is scattered across a dozen institutions, each with its own dashboard optimized to sell
you that institution's products - balances, cash flow, allocation, debt terms, tax situation, employer benefits. There are solutions out there (Mint's successors, Empower, Monarch), but they are usually paid and not necessarely aligned with your goals. Their advise can also be biased, a lead-gen funnel for an advisory arm.

## 2. Goals

**G1 — Complete picture.** One canonical, continuously-updated model of every asset,
liability, income stream, and recurring obligation.

**G2 — Goal-aware.** Advice is evaluated against explicitly stated, prioritized goals
with dates and dollar amounts — not generic best practice.

**G3 — Risk-calibrated.** Risk tolerance is captured as *both* stated preference and
revealed capacity (time horizon, income stability, emergency reserve depth), and the
tool flags when those two disagree.

**G4 — Reasoning is inspectable.** Every recommendation shows the inputs, the rule or
model that produced it, and what would change the answer. No black-box "do this."

**G5 — Advisory only.** The tool informs decisions; I execute them.

**G6 — Private by construction.** My code is public code. My data will be local, so that no third-party has access to it without my permission.

## 3. Non-goals

- ❌ **Executing transactions.** Not in v1, not in v5. See §4.
- ❌ **Multi-user / SaaS.** Single operator. Every design tradeoff resolves toward
  simplicity over scale.
- ❌ **Replacing a CPA or estate attorney.** The tool should *identify* when a
  professional is warranted and prepare the questions — not improvise the answer.
- ❌ **Day trading, market timing, stock picking.** Out of scope by conviction, not
  just effort. The tool's stance is asset-allocation-first.
- ❌ **Being a budgeting app.** Categorized spending is an *input*, not the product.
  If envelope budgeting is what's wanted, Actual Budget already exists.

## 4. Hard constraint: advisory-only, enforced architecturally

"The tool should not be able to act on my behalf" is the requirement most likely to
erode over time — it's always one convenient feature away. So it must be structural,
not a policy note.

**Three enforcement layers:**

1. **Credential layer.** Use data sources that are *read-only by protocol*, not
   read-only by permission scope. SimpleFIN's design goal is exactly this — "a window
   on a safe: it lets people look at, but not touch." A credential that cannot
   authorize a transfer cannot be misused by a bug, a prompt injection, or a future me
   in a hurry.
2. **Egress layer.** The process makes no authenticated outbound POST/PUT to any
   financial institution. Enforce with an allowlist at the HTTP-client wrapper and a
   test that fails if any institution client exposes a mutating method.
3. **Output layer.** Recommendations are rendered as *instructions for a human*
   ("move $X from A to B, here's why, here's the link") — never as a queued action
   with a confirm button. There is no execution surface to accidentally wire up.

> The LLM layer never gets tools that touch an institution. Its tools are read-only
> queries against the local database and calculators. This also neutralizes the
> obvious prompt-injection vector: a transaction memo that says "transfer $5000 to
> account X" reaches a model that has no capability to comply.

## 5. Users

One: me. Financially literate, technical, wants to understand the reasoning rather
than be told. Design implication: **prefer showing the model over hiding it.** The
tool should be able to say "here's the assumption I'm least confident in."

## 6. Core concepts

| Concept | Definition |
|---|---|
| **Account** | A container at an institution. Has type, tax treatment, balance, and for investments, holdings. |
| **Position** | A holding within an account, resolved to asset class, not just ticker. |
| **Cash flow** | Recurring or one-off income/expense, detected from transactions or declared. |
| **Goal** | Named objective: target amount, target date, priority, funding source, flexibility (hard date vs. aspirational). |
| **Risk profile** | Stated tolerance + computed capacity + observed behavior. |
| **Observation** | A machine-derived fact ("cash reserve = 2.1 months of expenses"). Deterministic. Carries a status (attention, ok, insufficient data, not applicable, error), the inputs and assumptions behind it, what's missing, and an annual dollar impact *only* when one follows from the inputs without speculation (D12). |
| **Recommendation** | A suggested action derived from observations, with rationale, magnitude of impact, and confidence. |
| **Decision** | My recorded response to a recommendation — taken, rejected, deferred — with reasoning. Feeds future advice. |

The **Observation → Recommendation → Decision** chain is the backbone. Observations
are computed by deterministic code and are auditable. Only the *framing and
prioritization* of recommendations involves an LLM. Decisions close the loop so the
tool stops re-suggesting things I've consciously declined.

## 7. Feature set

Structured on the [CFP Board's 7-step planning process](https://www.cfp.net/ethics/compliance-resources/2018/11/focus-on-ethics---the-7-step-financial-process)
(Circumstances → Goals → Analyze → Develop → Present → Implement → Monitor), which is
the established professional standard of care. Steps 1–5 and 7 are in scope; step 6
(Implement) is deliberately mine, per §4.

### Step 1 — Understand circumstances *(MVP)*

- **F1.1** Institution sync — balances, transactions, holdings, on a daily pull.
- **F1.2** Manual/declared assets — property, private equity, vehicles, crypto in
  cold storage, expected inheritances. Things no API will ever report.
- **F1.3** Transaction categorization with correction memory — my recategorizations
  become rules, not one-offs.
- **F1.4** Recurring-obligation detection — subscriptions, loan payments, true fixed
  cost baseline.
- **F1.5** Net worth statement + trend.
- **F1.6** Liability terms register — rate, remaining term, and critically
  whether each rate is fixed or variable. Manually entered; APIs rarely expose terms.
- **F1.7** Qualitative profile — employment stability, dependents, health coverage,
  state of residence (tax), employer benefits (401k match %, HSA eligibility, ESPP,
  vesting schedule). *This is the input most tools skip and the one that changes
  advice the most.*

### Step 2 — Identify & prioritize goals *(MVP)*

- **F2.1** Goal definition with target/date/priority/flexibility.
- **F2.2** Goal feasibility check — required monthly contribution vs. actual surplus.
- **F2.3** Conflict detection — when goals compete for the same dollars, surface the
  tradeoff explicitly rather than silently underfunding.
- **F2.4** Risk tolerance assessment — questionnaire *plus* computed capacity, with
  divergence flagged ("you describe yourself as aggressive; your 3-week cash reserve
  and variable income suggest lower capacity").

### Step 3 — Analyze current course *(MVP)*

Deterministic observation engine. Each check is a small, testable, independently
readable rule. **All ten are built (M1);** refinements made during implementation are
noted in italics.

- **F3.1** Emergency fund depth vs. target months.
- **F3.2** Cash drag — idle cash beyond reserve earning below money-market rates.
  *Measured against the 3-month Treasury bill rate (FRED DTB3); checking accounts keep
  one month of expenses out of the calculation for bills.*
- **F3.3** Tax-advantaged space utilization — 401k/IRA/HSA contributions vs. annual
  limits, employer match capture. *Unclaimed match is the highest-certainty return
  available and should always surface first.*
- **F3.4** Asset allocation vs. target — actual vs. policy, with drift magnitude.
- **F3.5** Asset *location* — tax-inefficient assets held in taxable accounts.
- **F3.6** Fee & expense-ratio audit — total portfolio cost, high-ER funds with
  cheaper equivalents.
- **F3.7** Concentration risk — single position, single sector, or employer stock
  exposure (the correlated-with-your-income case deserves its own flag).
  *Sector concentration isn't assessed yet: the securities catalog has no sector data.*
- **F3.8** Debt analysis — avalanche vs. snowball ordering, and refinance/payoff
  vs. invest comparisons on a risk-adjusted basis.
  *Built as a comparison with the risk-free Treasury bill rate rather than an assumed
  market return, which would bake a forecast into the finding. A credit card is never
  assumed to carry a balance: unless stated, no interest is estimated for it.*
- **F3.9** Insurance gap heuristics — life/disability/umbrella coverage vs. rough
  need. Flags for human review; does not price policies.
- **F3.10** Savings rate & runway.

### Step 4–5 — Develop & present recommendations *(MVP)*

- **F4.1** **Prioritized action list** — the primary artifact. Ranked by expected
  impact and certainty, not by how easy it was to compute. A guaranteed 50% employer
  match outranks a speculative allocation tweak, always.
- **F4.2** Rationale panel per recommendation: inputs used, rule applied, quantified
  benefit, what would change the answer.
- **F4.3** Confidence & assumption disclosure — explicitly names the assumption the
  recommendation is most sensitive to.
- **F4.4** Conversational Q&A over the full picture — "can I afford X?", "what
  happens to my goals if I take a 20% pay cut?"
- **F4.5** Scenario modeling — job loss, large purchase, market drawdown, rate
  change on variable debt.
- **F4.6** Decision log — record taken/rejected/deferred + why. Suppresses
  re-litigation and builds a personal record of *why* past choices were made.

### Step 7 — Monitor *(v1)*

- **F7.1** Periodic review digest — what changed, what it means, what's newly
  actionable.
- **F7.2** Drift & threshold alerts — allocation bands, reserve depletion, spending
  regime change.
- **F7.3** Goal progress tracking vs. required trajectory.
- **F7.4** Calendar-aware prompts — contribution deadlines, RSU vest dates, open
  enrollment, estimated tax dates.

### Later *(v2+)*

- **F8.1** Monte Carlo retirement projection with sequence-of-returns risk.
- **F8.2** Tax-loss harvesting candidate identification (flag only — wash-sale
  reasoning is genuinely hard and errors are expensive).
- **F8.3** Roth conversion window analysis.
- **F8.4** Multi-year withdrawal-order strategy.
- **F8.5** Backtesting the advice — did following it help?

## 8. Architecture

```
┌────────────────────────────────────────────────────────┐
│  CONNECTORS (read-only)                                 │
│  SimpleFIN · CSV/OFX import · manual entry              │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────┐
│  NORMALIZED STORE  (local, encrypted at rest)           │
│  accounts · transactions · positions · goals · profile  │
│  + decision log + full sync history                     │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────┐
│  OBSERVATION ENGINE  (pure functions, no LLM)           │
│  deterministic · unit-tested · fully auditable          │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────┐
│  ADVISORY LAYER  (LLM, read-only tools)                 │
│  prioritize · explain · converse · model scenarios      │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────┐
│  INTERFACE — local web UI + CLI. Renders advice only.   │
└────────────────────────────────────────────────────────┘
```

**The load-bearing decision: numbers come from code, narrative comes from the model.**
LLMs are unreliable arithmetic engines and excellent explainers. Every figure in a
recommendation traces to a deterministic calculation the observation engine produced;
the model selects, orders, and explains — it never computes. This makes the advice
testable and keeps hallucinated dollar amounts structurally impossible.

**Stack** (D3, D4) — Python (mature financial libraries, `Decimal` correctness),
SQLite + SQLCipher, FastAPI, and a plain server-rendered UI, with a CLI for scripted
use. Boring on purpose. Use `Decimal` for money everywhere; a float rounding bug in a
net-worth statement is a silent, corrosive failure. Alternatives in
[Appendix A.3](#a3--stack-d3).

**As built (M0–M1):** Python 3.11+, SQLite, and a Click CLI (`fa`). SQLCipher is
deferred (P4) and the web UI hasn't started. Money is an integer count of cents,
exposed as `Decimal` (D9). The engine's only I/O is assembling a *snapshot* —
database, profile, rules, cached benchmark rate — and every check is a pure function
of it, so checks are tested against hand-built snapshots with no database or clock.

## 9. Data sources

| Source | Role | Notes |
|---|---|---|
| **SimpleFIN Bridge** | Primary sync | ~$15/yr, read-only *by protocol*, MX-backed (16k+ institutions), 24 refreshes/day. Best fit for §4. **Recommended.** |
| **Plaid** | Alternative | Widest coverage and best DX, but built for fintechs; production access, per-call pricing, and a permission model that *can* include payment initiation. |
| **CSV / OFX / QFX import** | Fallback + backfill | Every institution supports it. Needed regardless — SimpleFIN won't cover everything, especially 401k providers and foreign accounts. *Built so far: CSV transaction and holdings exports, columns auto-detected (D14). OFX/QFX not yet.* |
| **Manual entry** | Real assets, terms | Property, private holdings, loan terms, benefits. |
| **Securities reference files** | Fund metadata | A few common funds in tracked `rules/securities.yml`; **the funds you actually hold are described in a local overlay outside the repo** (D11). |
| **FRED** | Benchmark rates | Free, official, no API key friction, no privacy exposure. Feeds F3.2. |
| **Historical return series** | Projections only | Deferred to M5. Not needed before Monte Carlo. |

**Start with CSV import, not an aggregator** (D1). It costs nothing, works with every
institution immediately, and lets the observation engine — the actual product — get
built and tested against real data on day one. Add sync once the analysis is worth
automating.

### 9.1 Market data: a local file, not a provider (D7)

The sync already returns current market *value* of holdings, so pricing is largely a
solved problem. What it does not return is what a holding *is* — and several checks
depend on that:

| Need | Feeds | Source |
|---|---|---|
| Expense ratios | F3.6 fee audit | `securities.yml` |
| Asset-class look-through | F3.4 allocation, F3.7 concentration | `securities.yml` |
| Money-market / T-bill yields | F3.2 cash drag | FRED |
| Historical return series | F8.1 Monte Carlo, F4.5 drawdown scenarios | Deferred to M5 |

**Why a file beats an API here.** The cardinality is tiny — on the order of 10–30
distinct securities, whose expense ratios and asset-class weights change approximately
never. A hand-maintained YAML file is a one-time effort, more accurate than a scraped
feed, versioned in git alongside the rules that consume it, and immune to a free tier
disappearing.

The privacy argument is stronger still: **querying a market-data API for your tickers
discloses your holdings to that provider.** That routes around the entire §10 posture
to retrieve facts that are public, static, and writable by hand. FRED is exempt because
the query is "what is the 3-month Treasury yield" — it reveals nothing about the
portfolio.

Look-through matters more than it sounds. A single target-date fund is a blend of
several underlying funds; without decomposition, allocation analysis reports one
unclassified position and F3.4 silently produces nothing useful. `securities.yml`
therefore stores fractional asset-class weights, not a single label.

> **Corrected in M1 (D11).** The original rule was that `securities.yml` is safe to
> track because it says what a fund *is*, never how much of it is held. That's
> necessary but not sufficient: the *set* of symbols in a public file discloses your
> holdings without a single share count — the same reasoning that had already ruled
> out per-institution import profiles. So the tracked file keeps a few common broad
> funds as reference, and the funds you own are described in `securities.local.yml`
> in the data directory, whose entries override the tracked ones. Quantities and
> values live in the database, also outside the repo.

## 10. Privacy & security requirements

Given the public repo, these are requirements, not aspirations. Full detail in
[SECURITY.md](../SECURITY.md).

- **P1** Personal data never lives in the working tree (P4). The deny-by-default
  `.gitignore` is defence in depth, not the primary barrier — and it cuts both ways:
  during M0 an unanchored `reports/` pattern silently excluded a source module from a
  commit that looked complete. CI now asserts every source file is tracked.
- **P2** Pre-commit secret scanning (`gitleaks`) + CI scanning + GitHub push
  protection. Local hooks fail open when bypassed; CI is the backstop.
  The hook is *adversarially tested* (`scripts/verify-hooks.sh`, locally and in CI):
  known-bad content must be blocked and known-good content allowed. Writing that test
  exposed silent failures a one-directional test would have missed.
- **P3** Credentials in the OS keychain past prototype stage. Never in `.env` long-term.
- **P4** **Data lives outside the repository**, at `~/.local/share/financial-advisor/`,
  mode `0700` (DB `0600`). This is structural rather than policy: a file that is not
  in the working tree cannot be committed by a mistaken `git add -A`, and the
  protection does not depend on `.gitignore` staying correct.
  *Encryption at rest is provided by FileVault (verified enabled).* Its limit,
  stated plainly: it protects a lost, stolen, or powered-off machine and does
  nothing against a process running as the logged-in user. SQLCipher would close
  that gap and is deferred — all database access routes through a single
  `db/connection.py`, so adopting it stays a one-file change plus a dump-and-reload.
  *Revisit trigger:* the DB needs to live on a synced or cloud-backed path, or
  untrusted code starts running on this machine.
- **P4a** **The working tree is inside iCloud Drive** (`~/Documents` has Desktop &
  Documents sync on). A database written next to the source would upload to Apple's
  servers and sync to every device on the account — silently, on first import, with
  FileVault providing no protection because the copy has already left the machine.
  This is the single most likely way this project leaks, and P4's data location is
  what prevents it.
- **P5** Synthetic test fixtures only. Never "anonymized" real exports — transaction
  timing and amount patterns are re-identifying on their own.
- **P6** No telemetry, no analytics, no crash reporting. Zero outbound calls except
  data sync and (if chosen) the model provider.
- **P7** **Model hosting: hybrid** (D2). The advisory layer sees the complete
  financial picture, making this the most consequential privacy call in the project.
  Resolved as a three-tier routing rule:
  1. **Deterministic engine** handles everything numeric. No model involved.
  2. **Local model** handles routine narrative — digests, phrasing, summaries.
  3. **Hosted API** is used deliberately, for genuinely hard reasoning, and only on
     redacted input (ratios and percentages, not balances) via P8.

  The escalation to tier 3 must be an explicit, logged decision — never an automatic
  fallback when the local model is uncertain. A silent fallback would make the privacy
  boundary depend on model confidence, which is exactly the wrong control.
  Alternatives in [Appendix A.2](#a2--model-hosting-d2).
- **P8** Redaction layer between the store and any external model — strips account
  numbers and institution names, and can express figures as ratios where the analysis
  doesn't need absolute values.
- **P9** Screenshots in the README/docs must use synthetic data. This is the most
  common way personal-finance projects leak.
- **P10** **Metadata is data.** Several M0–M1 decisions exist only because a harmless-
  looking artifact would disclose something: per-institution import profiles (where
  you bank), a tracked list of fund symbols (what you hold), account numbers echoed in
  error messages (the multi-account holdings error reports a count, never the values).
  Review new tracked files for what their *existence* reveals.
- **P11** Tests never read or write the real data directory. An autouse fixture
  redirects it for every test, because loading rules reads the local securities
  overlay from there.

## 11. Correctness, risk, and disclaimers

Bad financial advice is expensive and errors here are quiet. Requirements:

- **R1** Every observation rule is unit-tested against hand-computed fixtures.
- **R2** `Decimal` for all monetary arithmetic. No floats. *As built, enforced
  structurally rather than by lint:* `Money` refuses construction from, or
  multiplication by, a float; and the YAML loader builds `Decimal` directly from the
  source text, so hand-written profile and rules files never produce a float.
- **R3** Tax rules, contribution limits, and thresholds live in dated, versioned data
  files — never hardcoded in logic. They change annually and stale limits produce
  confidently wrong advice. *As built:* `rules/limits/<year>.yml` (2026 verified
  against IRS Notice 2025-67), `rules/thresholds.yml`, `rules/asset_classes.yml`. A
  missing year is refused, never borrowed; a missing threshold is an error, never a
  default.
- **R4** Recommendations touching tax, estate, or insurance carry an explicit
  "verify with a professional" flag. The tool's job there is to prepare the question.
- **R5** The tool must be able to say *"I don't have enough information"* and *"this
  depends on something I can't see."* Confident advice on incomplete data is the
  primary failure mode. *As built:* every check can return **insufficient data** with
  the exact input to provide. The first end-to-end run caught two checks breaking this
  rule — concentration said "fine" with no holdings imported, and asset location said
  "not applicable" when a 401(k)'s holdings were merely missing. Both are fixed and
  covered by tests.
- **R6** Personal-use posture. This is software analyzing my own data for my own
  decisions — not a service, no clients, no compensation for advice. Publishing the
  *code* is fine; if that ever changes and someone else's money is involved,
  investment-adviser regulation becomes a real question that needs real legal input
  before, not after.

## 12. Decisions

Settled. Rejected options and their reasoning are preserved in
[Appendix A](#appendix-a--alternatives-considered).

| # | Question | Decision |
|---|---|---|
| D1 | Data source to start with | **CSV import first.** SimpleFIN Bridge once the engine has proven it earns the subscription |
| D2 | Model hosting (§P7) | **Hybrid**, three-tier — deterministic → local → redacted hosted, escalation explicit and logged |
| D3 | Language & storage | **Python + SQLite/SQLCipher + FastAPI** |
| D4 | Interface | **Local web UI**, plus a CLI for scripted use |
| D5 | Scope of accounts in v1 | The **3–4 accounts holding most of the value**; full coverage later |
| D6 | Investment philosophy encoded | **Passive, allocation-first, low-cost.** Advice without an explicit stance is incoherent |
| D7 | Market data | **No provider.** Local `securities.yml` + FRED for rates; historical series deferred to M5 (§9.1) |
| D8 | Non-US accounts / multi-currency | **Out of scope. USD only.** Money is a single scalar amount; no currency column, no FX rates, no conversion layer |
| D9 | Money representation *(M0)* | **Integer cents** internally and in SQLite; `Decimal` at the API. `SUM()` over TEXT coerces to float, so integers keep SQL aggregation exact. Refines A.5 |
| D10 | Where the profile (F1.7) lives *(M1)* | **Hand-edited YAML in the data directory**, strictly validated: unknown keys are errors, every problem is reported at once, and a missing section ("not stated") is distinct from `null` ("none") |
| D11 | Securities catalog *(M1)* | **Tracked generic catalog plus a local overlay** for the funds you hold (§9.1 correction) |
| D12 | What the engine produces *(M1)* | **Observations, not recommendations.** Ranking and "what to do" are M2. Annual dollar impact is set only when it follows from the inputs without speculation — it will seed M2's ranking, where an invented number would silently corrupt the order |
| D13 | Schema changes *(M1)* | **Numbered SQL migrations, applied only by `fa init`, after a backup.** `connect()` refuses a database that's behind the code rather than upgrading it as a side effect |
| D14 | Import column mapping *(M0)* | **Auto-detected from headers**, not per-institution profiles (P10). A file that defeats detection gets a local, untracked override |
| D15 | Spending vs. transfers *(M1)* | **Pair equal-and-opposite movements between your own accounts; exclude unpaired transfer and investment-contribution wording but report the totals; count unmatched card and bill payments as spending; never treat peer-to-peer payments as transfers.** Keyword-based, and disclosed as such |

### 12.1 Enforcing the USD-only assumption

D8 removes a large amount of complexity, but an unstated assumption decays silently.
The failure mode is a non-USD figure entering the store and being summed as though it
were dollars — which produces a net worth that is simply wrong, with nothing visibly
broken.

So the assumption is enforced rather than assumed:

- **Import validation.** Any source declaring a currency code other than `USD` is
  **rejected at ingest with a loud error**, never coerced or silently accepted. A
  rejected import is a five-minute annoyance; a silently mis-summed one can go
  unnoticed for months.
- **No hidden conversion.** The system performs no FX conversion anywhere. If a
  foreign-denominated asset ever needs tracking, it is entered manually as a
  USD-valued declared asset (F1.2) with the valuation date recorded — an explicit,
  visibly-stale estimate, not a live figure pretending to be current.
- **Documented in the schema.** The money type carries a comment stating the
  invariant, so the assumption is discoverable by anyone reading the model — including
  a future me who has forgotten this conversation.

## 13. Milestones

**M0 — Foundation.** Repo, security tooling, schema, CSV import, net worth statement.
*Exit: real data loaded locally, nothing leaked.*
> ✅ **Built** 2026-09-03. Nothing has leaked; CI and the adversarially tested hook are
> green. ⏳ The "real data loaded" half of the exit hasn't happened: everything so
> far has run on synthetic data.

**M1 — Observation engine.** F3.1–F3.10 as tested pure functions. CLI report output.
*Exit: it tells me something true I didn't already know.*
> ✅ **Built** 2026-09-13: `fa check`, 265 tests with hand-computed expectations.
> ⏳ The exit can only be judged against real accounts. Next: fill in the profile,
> import the 3–4 largest accounts (D5), run `fa check`.

**M2 — Goals & prioritization.** F2.x + F4.1–F4.3. The ranked action list.
*Exit: I take an action because of it.*

**M3 — Advisory layer.** Conversational Q&A, scenarios, decision log.
*Exit: I ask it a real question and trust the answer enough to check it.*

**M4 — Automation & monitoring.** Sync, digests, alerts.
*Exit: it's useful without me opening it.*

**M5 — Projection & tax.** Monte Carlo, Roth analysis, harvesting flags.

## 14. Success criteria

The honest test isn't feature completion — it's whether the thing changes behavior.

1. **Did it surface something I didn't know?** (money left on the table, a fee, a
   concentration I hadn't priced)
2. **Did I take an action because of it?**
3. **Do I trust it enough to check it before a real decision?**
4. **Zero personal-data leaks to the public repo.** Binary, non-negotiable.
5. **Would its advice hold up if I read it back to a fee-only CFP?**

## 15. Implementation status

How each piece was built, and what went wrong along the way: [build log](blog/m0-m1-foundation-and-observation-engine.md).

| Feature | Status | Command |
|---|---|---|
| F1.1 Institution sync | ⏳ Not started (D1: CSV first) | — |
| F1.2 Declared assets | ✅ Manual accounts with hand-entered balances | `fa account-add --manual`, `fa balance` |
| F1.3 Categorization | ⏳ Not started; M1 needed only transfer detection (D15) | — |
| F1.4 Recurring obligations | ⏳ Not started | — |
| F1.5 Net worth | ✅ Statement with staleness flags · ⏳ trend | `fa networth` |
| F1.6 Liability terms | ✅ Rate, fixed/variable, minimum payment, promo end, revolving | `fa terms` |
| F1.7 Qualitative profile | ✅ Salary, match formula, IRA/HSA, allocation target, insurance · ⏳ state, ESPP, vesting | `fa profile` |
| Holdings | ✅ CSV import; tax lots combined; non-holding rows listed | `fa holdings` |
| Benchmark rate | ✅ FRED DTB3, cached | `fa rates` |
| F3.1–F3.10 | ✅ All ten checks | `fa check` |
| F2.x, F4.x, F7.x | ⏳ M2–M4 | — |

### 15.1 Known limitations

- **Transfer detection is keyword-based.** A contribution worded like "VANGUARD BUY
  ACH" isn't recognized as saving and counts as spending. The savings-rate check
  discloses what it excluded and counted under `fa check --verbose`.
- **Spending means total spending**, not essential spending, until categorization
  (F1.3) exists. Setting `monthly_essential_expenses` in the profile overrides it.
- **Monthly averages have edge effects.** A window that starts or ends mid-cycle
  counts a partial set of paydays.
- **CSV only** — no OFX/QFX, no sync.
- **Sector concentration** isn't assessed; the catalog has no sector data.
- **Display rounding** can make a flagged share look equal to its threshold (10.03%
  shown as 10.0% while flagged for exceeding 10%).
- **Net worth has no trend** yet.

---

## Appendix A — Alternatives considered

Options evaluated and rejected, kept so a future revisit starts from the argument
rather than from scratch. Each notes **what would change the answer** — the decisions
here are contingent on circumstances that may not hold forever.

### A.1 — Data source (D1)

| Option | Why not |
|---|---|
| **SimpleFIN Bridge** *(adopted later, not first)* | The right long-term primary source — read-only by protocol, ~$15/yr, MX-backed. Deferred only because paying and integrating before the observation engine exists optimizes the wrong end of the system. |
| **Plaid** | Widest coverage and the best developer experience, but built for fintechs: production access review, per-call pricing that gets unpredictable, and — decisively — a permission model that *can* extend to payment initiation. §4 wants a credential that is structurally incapable of moving money, not one that merely isn't configured to. |
| **MX direct** | Effectively what SimpleFIN resells, without the personal-use pricing or the read-only protocol guarantee. |
| **Finicity** | Optimized for lending, underwriting, and mortgage verification. Wrong shape for personal planning, custom pricing, no personal tier. |
| **Institution APIs directly** | Almost no US retail institution offers one to individuals. |
| **Screen scraping** | Brittle, frequently violates terms of service, and requires storing full-access credentials — the precise thing §4 exists to avoid. |

**What would change this:** if manual CSV export becomes a recurring chore across more
than a handful of institutions, the SimpleFIN subscription pays for itself immediately.
That's the trigger, not a date.

### A.2 — Model hosting (D2)

| Option | Why not |
|---|---|
| **Hosted API only** | Best reasoning quality by a clear margin, and simplest to build. Rejected because it sends a complete, unredacted financial profile — balances, employer, goals, debts — to a third party by default. For a system whose stated first principle is that data stays local, that's the wrong default even with a good provider. |
| **Local model only** | Maximum privacy, zero egress. Rejected on capability: the hard questions (multi-goal tradeoffs, scenario reasoning, tax-adjacent judgment) are exactly where locally-runnable models are weakest, and confidently-wrong financial reasoning is the failure mode §R5 is written to prevent. Being private and wrong is not a win. |
| **Hosted API with zero-retention agreement** | Meaningfully better than the default, and worth revisiting. Still requires trusting a contractual control rather than a structural one; the hybrid gets most of the benefit without the trust assumption. |

**What would change this:** materially stronger local models — the hybrid's tier 3
exists only to cover a capability gap. If that gap closes, tier 3 should be deleted
rather than kept around.

### A.3 — Stack (D3)

| Option | Why not |
|---|---|
| **TypeScript / Node** | Better UI story and a single language end-to-end. Rejected primarily on money arithmetic: JS has no native decimal type, so correctness depends on remembering to use a library at every call site. §R2 wants the safe path to be the default one, not the disciplined one. |
| **Postgres** | Warranted for concurrent multi-user access — explicitly a non-goal (§3). Adds a service to run and back up for a single-user local tool. SQLite is a file, which also makes encryption-at-rest and backup trivial. |
| **Rust / Go** | Excellent correctness properties, far weaker financial and data-analysis ecosystems. Wrong trade for a project that is mostly rules and reporting. |
| **Jupyter notebooks** | Fastest possible start and genuinely tempting for the analysis work. Rejected because §R1 requires the observation engine to be unit-tested, and notebooks resist that — hidden execution-order state is a bad foundation for advice acted on with real money. |

### A.4 — Market data (D7)

| Option | Why not |
|---|---|
| **yfinance / Yahoo Finance** | Free and comprehensive, but an unofficial scraper of an undocumented endpoint: breaks without warning, and its terms-of-service position is murky. A dependency that fails silently is worse than no dependency when the output is advice. |
| **Alpha Vantage / Tiingo / EODHD free tiers** | Real APIs with real docs. Rejected as unnecessary: they solve a data-volume problem this project doesn't have, and each one adds a party that learns the holdings list. |
| **Polygon / paid feeds** | Priced and engineered for trading systems. Enormous overkill for static fund metadata. |
| **SEC EDGAR / N-PORT filings** | Free and authoritative for fund composition — the correct answer at scale. Parsing N-PORT to learn a handful of expense ratios that could be typed by hand is effort spent in the wrong place. |

**What would change this:** M5. Monte Carlo needs historical return series, which
genuinely cannot be hand-maintained — that's the point to select a real provider, and
the choice can be made then with the requirement actually in hand.

### A.5 — Multi-currency (D8)

All accounts are USD-denominated, so currency handling is out of scope entirely.

| Option | Why not |
|---|---|
| **Full multi-currency** — currency on every amount, FX rate table, conversion at read time | The general solution, and unnecessary here. It complicates every monetary column, forces a reporting-currency decision on every aggregation, and introduces rate-staleness as a permanent correctness concern. Cost with no corresponding benefit. |
| **Currency column, always `USD`** | The tempting middle ground: "cheap insurance." Rejected because it is insurance that doesn't pay out — a column alone doesn't convert anything, so adding real currencies later still requires the FX layer and the aggregation rework. Meanwhile every query carries a dimension that is constant, and the presence of the field implies a capability that doesn't exist. Better to state the invariant plainly (§12.1) than to gesture at flexibility that isn't there. |
| **Store a minor-unit integer (cents) instead of `Decimal`** | A legitimate alternative that avoids float issues equally well. `Decimal` wins on readability of the rules in §7 Step 3, which are the code most likely to be read and audited by hand, and SQLite has no native decimal type either way. |

**What would change this:** acquiring a genuinely foreign-denominated account —
not merely an overseas one. The import validation in §12.1 is what surfaces that
moment loudly instead of silently, and the rework at that point is a schema migration
plus re-verification of the aggregations, which is the cost knowingly accepted here.

### A.6 — Rejected framings

Two shapes this project could have taken, recorded because they're the obvious
suggestions and the reasons against them are the reasons the design looks like it does.

**Extend an existing tool (Firefly III, Actual Budget) rather than build.** Both are
mature, self-hosted, and solve ingestion and categorization well. Rejected because both
are fundamentally *ledgers* — they answer "where did the money go," and their data
models are built around transactions and budgets, not goals, positions, asset classes,
and risk capacity. The advisory layer is the entire product here, and it would sit
awkwardly on top of a schema designed for a different question. Worth reconsidering
narrowly: importing their CSV normalization logic rather than the application.

**LLM reads raw statements and gives advice directly.** Dramatically less code — hand
the model everything and ask. Rejected on two independent grounds. It makes every
number a potential hallucination in a domain where a wrong figure is expensive and
quiet; and it forfeits G4, because there's no inspectable rule behind a recommendation,
only a fluent explanation that may be post-hoc. The Observation → Recommendation
split in §6 exists specifically so the numbers are testable and the reasoning is
auditable.

### A.7 — Implementation decisions (D9–D15)

| Decision | Option not taken | Why not |
|---|---|---|
| D9 | Money as decimal TEXT in SQLite | Readable in the database, but `SUM()` coerces TEXT to float — putting a float back into the net-worth path through aggregation. |
| D10 | Profile as database rows, edited through CLI flags | Tiered match formulas and target allocations are clumsy as flags and rows. A YAML file can be read and reviewed as a whole; the cost is validation, which is why the loader is strict. |
| D10 | Profile in a gitignored `config/local.yml` inside the repo | The working tree is iCloud-synced (P4a): gitignored doesn't mean local. |
| D11 | A single tracked catalog | Discloses holdings through the list of symbols (§9.1). |
| D13 | Migrate automatically on connect | An upgrade rewrites tables holding the only copy of the data. It should happen on an explicit command, after a backup — not as a side effect of listing accounts. |
| D14 | Per-institution mapping files in the repo | Disclose where you bank (P10). |
| D15 | Exclude every row that mentions a payment | Makes spending on an unimported card vanish: its payment is the only trace of those purchases. |
| D15 | Treat Zelle and Venmo as transfers | Rent paid by Zelle is spending. |

**What would change this:** categorization with correction memory (F1.3) would let
D15 rely on learned rules instead of keywords.

---

### Sources

- [CFP Board — The 7-Step Financial Planning Process](https://www.cfp.net/ethics/compliance-resources/2018/11/focus-on-ethics---the-7-step-financial-planning-process)
- [Kitces — CFP Board's Financial Planning Practice Standards](https://www.kitces.com/blog/definition-financial-planning-practice-standards-conduct-required-cfp-board/)
- [SimpleFIN Protocol](https://www.simplefin.org/protocol.html)
- [Open Banking Compare — Best Open Banking API Providers for Developers (2026)](https://www.openbankingcompare.com/blog/best-open-banking-api-providers-for-developers-2026)
- [Plaid vs MX vs Finicity](https://fintechspecs.com/blog/plaid-vs-mx-vs-finicity/)
