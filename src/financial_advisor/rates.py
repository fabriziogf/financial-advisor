"""Benchmark cash yield from FRED (PRD §9.1, F3.2).

The one outbound request the analysis layer makes, and it discloses nothing: the
query is "what is the 3-month Treasury bill rate", with no parameter that describes
you. No API key, and a generic User-Agent.

Fetching is kept apart from analysis. Checks read a cached value passed in through
the snapshot and never touch the network, which is what keeps them pure and testable.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen

from .paths import data_dir, ensure_data_dir

__all__ = [
    "BenchmarkRate",
    "RatesError",
    "SERIES",
    "SERIES_LABEL",
    "parse_fred_csv",
    "fetch_benchmark",
    "save_benchmark",
    "load_benchmark",
    "cache_path",
]

SERIES = "DTB3"
SERIES_LABEL = "3-month Treasury bill rate (FRED DTB3)"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
CACHE_FILENAME = "benchmark_rate.json"


class RatesError(Exception):
    pass


@dataclass(frozen=True)
class BenchmarkRate:
    series: str
    observed_on: date
    rate: Decimal  # fraction: 0.0386 is 3.86%
    fetched_on: date


def parse_fred_csv(text: str, series: str = SERIES) -> tuple[date, Decimal]:
    """Latest non-missing observation, as (date, fraction).

    FRED leaves market holidays blank (and older responses use "."). Reading those
    as zero would report a 0% benchmark every long weekend, and every cash account
    would suddenly look competitive.
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2 or header[1].strip().upper() != series:
        raise RatesError(f"unexpected FRED response header: {header!r}")

    latest: tuple[date, Decimal] | None = None
    for row in reader:
        if len(row) < 2:
            continue
        raw = row[1].strip()
        if raw in ("", "."):
            continue
        try:
            observed, value = date.fromisoformat(row[0].strip()), Decimal(raw)
        except (ValueError, InvalidOperation) as exc:
            raise RatesError(f"unreadable FRED row: {row!r}") from exc
        if latest is None or observed > latest[0]:
            latest = (observed, value)

    if latest is None:
        raise RatesError("FRED response contained no observations")
    observed, percent = latest
    if not Decimal(0) <= percent < Decimal(25):
        raise RatesError(f"implausible {series} value: {percent}%")
    return observed, percent / 100


def fetch_benchmark(
    *, today: date, opener: Callable[..., object] = urlopen, timeout: int = 20
) -> BenchmarkRate:
    request = Request(
        FRED_CSV_URL.format(series=SERIES), headers={"User-Agent": "financial-advisor"}
    )
    try:
        with opener(request, timeout=timeout) as response:  # type: ignore[attr-defined]
            text = response.read().decode("utf-8")
    except OSError as exc:  # URLError and HTTPError are OSErrors
        raise RatesError(f"could not reach FRED: {exc}") from exc
    observed, rate = parse_fred_csv(text)
    return BenchmarkRate(series=SERIES, observed_on=observed, rate=rate, fetched_on=today)


def cache_path() -> Path:
    return data_dir() / CACHE_FILENAME


def save_benchmark(rate: BenchmarkRate, path: Path | None = None) -> Path:
    ensure_data_dir()
    target = path or cache_path()
    target.write_text(
        json.dumps(
            {
                "series": rate.series,
                "observed_on": rate.observed_on.isoformat(),
                "rate": str(rate.rate),
                "fetched_on": rate.fetched_on.isoformat(),
            },
            indent=2,
        )
    )
    target.chmod(0o600)
    return target


def load_benchmark(path: Path | None = None) -> BenchmarkRate | None:
    target = path or cache_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
        return BenchmarkRate(
            series=data["series"],
            observed_on=date.fromisoformat(data["observed_on"]),
            rate=Decimal(data["rate"]),
            fetched_on=date.fromisoformat(data["fetched_on"]),
        )
    except (ValueError, KeyError, InvalidOperation) as exc:
        raise RatesError(
            f"cached benchmark at {target} is unreadable; run `fa rates refresh`"
        ) from exc
