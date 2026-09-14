"""F3.10 — Savings rate and runway, from imported transactions."""

from __future__ import annotations

from ...money import Money
from ..model import Fact, Observation, Severity, fmt_months, fmt_pct
from ..snapshot import Snapshot
from ._common import attention, insufficient, ok

TITLE = "Savings rate"
KEY = "F3.10.rate"
LAGGING_DAYS = 35


def check(snapshot: Snapshot) -> list[Observation]:
    thresholds = snapshot.thresholds
    min_days = thresholds.integer("cashflow", "min_days_of_history")
    flow = snapshot.cashflow
    if flow is None:
        return [
            insufficient(
                KEY,
                TITLE,
                "No checking, savings, or credit card transactions are imported.",
                ["Import transaction exports with `fa import FILE --account NAME`."],
            )
        ]
    if not flow.is_reliable(min_days):
        return [
            insufficient(
                KEY,
                TITLE,
                f"The shortest account history is {flow.shortest_coverage_days} days; at least "
                f"{min_days} are needed for a stable monthly average.",
                [f"Import at least {min_days} days of history for each spending account."],
            )
        ]
    if flow.monthly_income.cents <= 0:
        return [
            insufficient(
                KEY,
                TITLE,
                "No income deposits were found in the imported accounts.",
                ["Import the account your pay is deposited into."],
            )
        ]

    workplace = Money(0)
    profile = snapshot.profile
    if (
        profile is not None
        and profile.employer_plan is not None
        and profile.employer_plan.contribution_rate is not None
        and profile.annual_salary is not None
    ):
        workplace = profile.annual_salary * (profile.employer_plan.contribution_rate / 12)

    income = flow.monthly_income + workplace
    saved = income - flow.monthly_spending
    rate = saved.ratio_to(income)

    facts = [Fact("Take-home income", f"{flow.monthly_income.format()}/mo")]
    if workplace:
        facts.append(Fact("Workplace retirement contributions", f"{workplace.format()}/mo"))
    facts += [
        Fact("Spending", f"{flow.monthly_spending.format()}/mo"),
        Fact("Saved", f"{saved.format()}/mo"),
        Fact("Savings rate", fmt_pct(rate)),
    ]
    liquid = snapshot.liquid_total()
    if liquid is not None and flow.monthly_spending.cents > 0:
        runway = fmt_months(liquid.ratio_to(flow.monthly_spending))
        facts.append(Fact("Runway", f"{runway} months of spending in liquid cash"))

    assumptions = [
        "Income is pay as deposited (after tax and payroll deductions) plus workplace "
        "retirement contributions from your profile; employer match is excluded.",
        f"Averages cover {flow.first_date} to {flow.last_date}. Bonuses, annual bills, and "
        "one-off purchases distort shorter windows.",
    ]
    if flow.paired_transfer_count:
        assumptions.append(
            f"{flow.paired_transfer_count} transfer(s) between your own accounts, totaling "
            f"{flow.paired_transfer_total.format()}, are excluded from both income and spending."
        )
    if flow.unpaired_transfers_out or flow.unpaired_transfers_in:
        assumptions.append(
            "Transfers to or from accounts that aren't imported are excluded: "
            f"{flow.unpaired_transfers_out.format()} out, {flow.unpaired_transfers_in.format()} in. "
            "If any of that was really spending or income, the rate is off by that much."
        )
    if flow.unpaired_payments_counted:
        assumptions.append(
            f"{flow.unpaired_payments_counted.format()} of payments to cards that aren't imported "
            "is counted as spending — it's the only record of those purchases."
        )
    lagging = [c for c in flow.coverage if c.lag_days > LAGGING_DAYS]
    if lagging:
        listed = ", ".join(f"{snapshot.account_name(c.account_id)} ({c.last})" for c in lagging)
        assumptions.append(
            f"Transactions for {listed} end well before the newest data, which dilutes their "
            "monthly figures. A fresh export fixes it."
        )
    inputs = ("Imported transactions", "employer_plan.contribution_percent", "income.annual_salary")

    low = thresholds.decimal("savings", "low_savings_rate")
    if saved.is_negative():
        return [
            attention(
                KEY,
                TITLE,
                Severity.HIGH,
                f"Spending of {flow.monthly_spending.format()} a month exceeds income of "
                f"{income.format()} by {abs(saved).format()}.",
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]
    if rate < low:
        return [
            attention(
                KEY,
                TITLE,
                Severity.LOW,
                f"You save {fmt_pct(rate)} of income ({saved.format()} a month), below "
                f"{fmt_pct(low)}.",
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]
    return [
        ok(
            KEY,
            TITLE,
            f"You save {fmt_pct(rate)} of income, about {saved.format()} a month.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
        )
    ]
