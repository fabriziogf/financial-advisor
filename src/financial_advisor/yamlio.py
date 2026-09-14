"""YAML loading that never produces a float.

Profile, rules, and securities files are hand-edited, so numbers in them get written
bare — `annual_salary: 150000.00`, `expense_ratio: 0.0003`. PyYAML's safe loader
turns those into floats, which would put floats into the money and rate paths
through the side door R2 closed at the front.

This loader constructs `Decimal` directly from the scalar's source text instead, so
the value typed is the value used, exactly. Non-finite values (`.inf`, `.nan`) are
refused rather than carried into arithmetic.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

__all__ = ["DecimalSafeLoader", "load_yaml", "load_yaml_text"]


class DecimalSafeLoader(yaml.SafeLoader):
    """SafeLoader with floats resolved to Decimal."""


def _construct_decimal(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> Decimal:
    text = str(loader.construct_scalar(node))
    try:
        value = Decimal(text.replace("_", ""))
    except InvalidOperation as exc:
        raise ConstructorError(
            None, None, f"could not read {text!r} as an exact number", node.start_mark
        ) from exc
    if not value.is_finite():
        raise ConstructorError(None, None, f"refusing non-finite number {text!r}", node.start_mark)
    return value


DecimalSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def load_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=DecimalSafeLoader)  # noqa: S506 - SafeLoader subclass


def load_yaml(path: Path) -> Any:
    return load_yaml_text(path.read_text(encoding="utf-8"))
