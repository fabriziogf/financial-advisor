# PRD — Personal Financial Advisor

| | |
|---|---|
| **Status** | Draft v0.1 — open for editing |
| **Author** | fabriziogf |
| **Last updated** | 2026-08-13 |
| **Repo** | Public. See [SECURITY.md](../SECURITY.md). |

> **How to read this.** Items marked `❓ DECISION NEEDED` are places where I made a
> recommendation but the call is yours; they're collected in §12. Everything else is a
> proposal, not a commitment — edit freely.

---

## 1. Problem

Money decisions are made with partial information. The data needed to decide well —
balances, cash flow, allocation, debt terms, tax situation, employer benefits — is
scattered across a dozen institutions, each with its own dashboard optimized to sell
you that institution's products. Aggregators (Mint's successors, Empower, Monarch)
solve the *gathering* problem but stop at description: charts of where money went.
They don't reason about *your* goals, and their advice, where it exists, is
conflicted — it's a lead-gen funnel for an advisory arm.

What's missing is the analysis layer: something that holds the complete picture,
knows the goals and the risk tolerance, and answers **"given all of this, what should
I do next?"** with reasoning I can inspect and argue with.

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

**G6 — Private by construction.** Public code, local data, no third-party sees the
whole picture without an explicit decision.

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

**Recommended stack** — Python (financial libraries, `pandas`, decimal correctness),
SQLite + SQLCipher, FastAPI, and a plain server-rendered UI. Boring on purpose. Use
`Decimal` for money everywhere; a float rounding bug in a net-worth statement is a
silent, corrosive failure. ❓

## 9. Data sources

| Source | Role | Notes |
|---|---|---|
| **SimpleFIN Bridge** | Primary sync | ~$15/yr, read-only *by protocol*, MX-backed (16k+ institutions), 24 refreshes/day. Best fit for §4. **Recommended.** |
| **Plaid** | Alternative | Widest coverage and best DX, but built for fintechs; production access, per-call pricing, and a permission model that *can* include payment initiation. |
| **CSV / OFX / QFX import** | Fallback + backfill | Every institution supports it. Needed regardless — SimpleFIN won't cover everything, especially 401k providers and foreign accounts. |
| **Manual entry** | Real assets, terms | Property, private holdings, loan terms, benefits. |
| **Market data** | Pricing, expense ratios | ❓ provider TBD. |

**Start with CSV import, not an aggregator.** It costs nothing, works with every
institution immediately, and lets the observation engine — the actual product — get
built and tested against real data on day one. Add sync once the analysis is worth
automating. ❓

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
- **P7** ❓ **Model hosting decision.** The advisory layer necessarily sees the
  complete financial picture. Three options, and this is the single most consequential
  privacy call in the project:
  - *Hosted API (Claude/OpenAI)* — best reasoning quality; provider sees the data.
  - *Local model (Ollama)* — nothing leaves the machine; materially weaker reasoning.
  - *Hybrid* — deterministic engine handles everything numeric, local model handles
    routine narrative, hosted API used deliberately for hard questions on redacted
    inputs (ratios and percentages rather than balances). More work, best tradeoff.
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

## 12. ❓ Decisions needed

| # | Question | My recommendation |
|---|---|---|
| D1 | Data source to start with | **CSV import first**, SimpleFIN Bridge once the engine proves useful |
| D2 | Model hosting (§P7) | **Hybrid** — deterministic numbers, redacted inputs to a hosted model for hard reasoning |
| D3 | Language/stack | **Python + SQLite + FastAPI** |
| D4 | Interface | **Local web UI**, CLI for scripted use |
| D5 | Scope of accounts in v1 | Start with the 3–4 that hold most of the value; full coverage later |
| D6 | Investment philosophy the tool encodes | **Passive, allocation-first, low-cost.** The tool needs an explicit stance — advice without one is incoherent |
| D7 | Market data provider | TBD |
| D8 | Does this handle non-US accounts / multi-currency? | Affects the data model significantly — decide before schema work |

## 13. Milestones

**M0 — Foundation.** Repo, security tooling, schema, CSV import, net worth statement.
*Exit: real data loaded locally, nothing leaked.*

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

### Sources

- [CFP Board — The 7-Step Financial Planning Process](https://www.cfp.net/ethics/compliance-resources/2018/11/focus-on-ethics---the-7-step-financial-planning-process)
- [Kitces — CFP Board's Financial Planning Practice Standards](https://www.kitces.com/blog/definition-financial-planning-practice-standards-conduct-required-cfp-board/)
- [SimpleFIN Protocol](https://www.simplefin.org/protocol.html)
- [Open Banking Compare — Best Open Banking API Providers for Developers (2026)](https://www.openbankingcompare.com/blog/best-open-banking-api-providers-for-developers-2026)
- [Plaid vs MX vs Finicity](https://fintechspecs.com/blog/plaid-vs-mx-vs-finicity/)
