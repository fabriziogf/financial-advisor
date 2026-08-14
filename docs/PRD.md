# PRD — Personal Financial Advisor

| | |
|---|---|
| **Status** | Draft v0.2 — foundational decisions resolved |
| **Author** | fabriziogf |
| **Last updated** | 2026-08-13 |
| **Repo** | Public. See [SECURITY.md](../SECURITY.md). |

> **How to read this.** §12 records the decisions that are settled and the one that
> isn't (D8, multi-currency — it blocks schema work). Options that were considered and
> rejected are preserved in [Appendix A](#appendix-a--alternatives-considered), with
> the reasoning, so a future revisit starts from the argument rather than from scratch.

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
| **Observation** | A machine-derived fact ("cash reserve = 2.1 months of expenses"). Deterministic. |
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
readable rule:

- **F3.1** Emergency fund depth vs. target months.
- **F3.2** Cash drag — idle cash beyond reserve earning below money-market rates.
- **F3.3** Tax-advantaged space utilization — 401k/IRA/HSA contributions vs. annual
  limits, employer match capture. *Unclaimed match is the highest-certainty return
  available and should always surface first.*
- **F3.4** Asset allocation vs. target — actual vs. policy, with drift magnitude.
- **F3.5** Asset *location* — tax-inefficient assets held in taxable accounts.
- **F3.6** Fee & expense-ratio audit — total portfolio cost, high-ER funds with
  cheaper equivalents.
- **F3.7** Concentration risk — single position, single sector, or employer stock
  exposure (the correlated-with-your-income case deserves its own flag).
- **F3.8** Debt analysis — avalanche vs. snowball ordering, and refinance/payoff
  vs. invest comparisons on a risk-adjusted basis.
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

## 9. Data sources

| Source | Role | Notes |
|---|---|---|
| **SimpleFIN Bridge** | Primary sync | ~$15/yr, read-only *by protocol*, MX-backed (16k+ institutions), 24 refreshes/day. Best fit for §4. **Recommended.** |
| **Plaid** | Alternative | Widest coverage and best DX, but built for fintechs; production access, per-call pricing, and a permission model that *can* include payment initiation. |
| **CSV / OFX / QFX import** | Fallback + backfill | Every institution supports it. Needed regardless — SimpleFIN won't cover everything, especially 401k providers and foreign accounts. |
| **Manual entry** | Real assets, terms | Property, private holdings, loan terms, benefits. |
| **Local securities reference file** | Fund metadata | Hand-maintained `rules/securities.yml`. **Tracked in git — it describes funds, not holdings.** |
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

> **This file is tracked in git.** It is reference data about publicly-traded
> instruments — it says what VTI *is*, never that I hold any. Quantities, values, and
> account associations live in `data/`. Keep that boundary strict: the moment a share
> count appears in this file, it stops being reference data.

## 10. Privacy & security requirements

Given the public repo, these are requirements, not aspirations. Full detail in
[SECURITY.md](../SECURITY.md).

- **P1** All personal data in gitignored local paths. Deny-by-default `.gitignore`.
- **P2** Pre-commit secret scanning (`gitleaks`) + CI scanning + GitHub push
  protection. Local hooks fail open when bypassed; CI is the backstop.
- **P3** Credentials in the OS keychain past prototype stage. Never in `.env` long-term.
- **P4** Database encrypted at rest.
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

## 11. Correctness, risk, and disclaimers

Bad financial advice is expensive and errors here are quiet. Requirements:

- **R1** Every observation rule is unit-tested against hand-computed fixtures.
- **R2** `Decimal` for all monetary arithmetic. No floats. Enforced by lint rule.
- **R3** Tax rules, contribution limits, and thresholds live in dated, versioned data
  files — never hardcoded in logic. They change annually and stale limits produce
  confidently wrong advice.
- **R4** Recommendations touching tax, estate, or insurance carry an explicit
  "verify with a professional" flag. The tool's job there is to prepare the question.
- **R5** The tool must be able to say *"I don't have enough information"* and *"this
  depends on something I can't see."* Confident advice on incomplete data is the
  primary failure mode.
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

### ❓ Still open

| # | Question | Why it matters |
|---|---|---|
| D8 | Non-US accounts / multi-currency in scope? | **Blocks M0.** Multi-currency is not a feature you add later — it changes the money type, every balance and transaction row, and every aggregation in the observation engine. Retrofitting it means rewriting the schema and re-verifying every calculation in §7 Step 3. Decide before schema work begins |

## 13. Milestones

**M0 — Foundation.** Repo, security tooling, schema, CSV import, net worth statement.
*Exit: real data loaded locally, nothing leaked.*
> ⚠️ **Blocked on D8.** Schema work cannot start until the multi-currency question is
> settled — it determines the money type that every other table depends on.

**M1 — Observation engine.** F3.1–F3.10 as tested pure functions. CLI report output.
*Exit: it tells me something true I didn't already know.*

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

### A.5 — Rejected framings

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

---

### Sources

- [CFP Board — The 7-Step Financial Planning Process](https://www.cfp.net/ethics/compliance-resources/2018/11/focus-on-ethics---the-7-step-financial-planning-process)
- [Kitces — CFP Board's Financial Planning Practice Standards](https://www.kitces.com/blog/definition-financial-planning-practice-standards-conduct-required-cfp-board/)
- [SimpleFIN Protocol](https://www.simplefin.org/protocol.html)
- [Open Banking Compare — Best Open Banking API Providers for Developers (2026)](https://www.openbankingcompare.com/blog/best-open-banking-api-providers-for-developers-2026)
- [Plaid vs MX vs Finicity](https://fintechspecs.com/blog/plaid-vs-mx-vs-finicity/)
