"""Independent Decimal oracle for the hand-calculated fixture.

These tests intentionally do not import Spark or production transformations. A
pipeline integration check must compare its output against EXPECTED below.
Run: python -m unittest discover -s tests -p test_business_oracle.py -v
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import unittest


FIXTURE = Path(__file__).parent / "fixtures" / "lines.json"
CENT = Decimal("0.01")
NON_MERCHANDISE_CODES = frozenset(
    {
        "POST", "DOT", "M", "M2", "C2", "D", "B", "ADJUST", "ADJUST2",
        "BANK CHARGES", "AMAZONFEE", "CRUK", "TEST001", "TEST002",
    }
)
EXPECTED = {
    "received_deliveries": 18,
    "source_rows": 16,
    "repeat_deliveries": 2,
    "accepted_rows": 8,
    "quarantined_rows": 7,
    "excluded_rows": 1,
    "parseable_source_rows": 15,
    "unparseable_source_rows": 1,
    "gross_positive_gbp": Decimal("48.51"),
    "negative_adjustments_gbp": Decimal("-13.51"),
    "net_recorded_gbp": Decimal("35.00"),
    "positive_units": 14,
    "known_customer_rows": 6,
    "quarantined_parseable_gbp": Decimal("11.50"),
    "excluded_parseable_gbp": Decimal("5.00"),
    "source_parseable_gbp": Decimal("51.50"),
    # Explicitly unsafe sum over accepted deliveries before source-row dedup.
    "unsafe_accepted_delivery_gbp": Decimal("62.50"),
}


def source_identity(row: dict) -> str:
    """Identity has no invoice/product/customer or delivery metadata in it."""
    components = (row["source_sha256"], row["source_sheet"], str(row["source_row"]))
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _amount(row: dict) -> Decimal | None:
    quantity = _number(row.get("quantity"))
    price = _number(row.get("unit_price"))
    if quantity is None or price is None:
        return None
    return (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)


def _disposition(row: dict) -> str:
    invoice = str(row.get("invoice") or "").strip()
    stock_code = str(row.get("stock_code") or "").strip().upper()
    quantity = _number(row.get("quantity"))
    price = _number(row.get("unit_price"))
    try:
        datetime.fromisoformat(str(row.get("event_time")))
        valid_time = True
    except ValueError:
        valid_time = False
    valid = (
        invoice
        and stock_code
        and valid_time
        and quantity is not None
        and quantity == quantity.to_integral_value()
        and quantity != 0
        and price is not None
        and price >= 0
        and not (invoice.upper().startswith("C") and quantity > 0)
    )
    if not valid:
        return "quarantined"
    if stock_code in NON_MERCHANDISE_CODES:
        return "excluded"
    return "accepted"


def oracle(deliveries: list[dict]) -> dict:
    """Return hand-fixture oracle metrics; not a production batch fallback."""
    unique: dict[str, dict] = {}
    for row in deliveries:
        identity = source_identity(row)
        if identity in unique and row != unique[identity]:
            raise ValueError("Conflicting source-row payload needs an explicit revision")
        unique.setdefault(identity, row)
    by_disposition: dict[str, list[dict]] = {
        "accepted": [], "quarantined": [], "excluded": []
    }
    for row in unique.values():
        by_disposition[_disposition(row)].append(row)
    accepted = by_disposition["accepted"]
    accepted_amounts = [_amount(row) for row in accepted]
    source_amounts = [_amount(row) for row in unique.values()]
    result = {
        "received_deliveries": len(deliveries),
        "source_rows": len(unique),
        "repeat_deliveries": len(deliveries) - len(unique),
        "accepted_rows": len(accepted),
        "quarantined_rows": len(by_disposition["quarantined"]),
        "excluded_rows": len(by_disposition["excluded"]),
        "parseable_source_rows": sum(value is not None for value in source_amounts),
        "unparseable_source_rows": sum(value is None for value in source_amounts),
        "gross_positive_gbp": sum((v for v in accepted_amounts if v > 0), Decimal(0)),
        "negative_adjustments_gbp": sum((v for v in accepted_amounts if v < 0), Decimal(0)),
        "net_recorded_gbp": sum(accepted_amounts, Decimal(0)),
        "positive_units": sum(int(row["quantity"]) for row in accepted if _number(row["quantity"]) > 0),
        "known_customer_rows": sum(row.get("customer_id") not in (None, "") for row in accepted),
        "source_parseable_gbp": sum((v for v in source_amounts if v is not None), Decimal(0)),
        "unsafe_accepted_delivery_gbp": sum(
            (_amount(row) for row in deliveries if _disposition(row) == "accepted"), Decimal(0)
        ),
    }
    for disposition in ("quarantined", "excluded"):
        result[f"{disposition}_parseable_gbp"] = sum(
            (amount for row in by_disposition[disposition] if (amount := _amount(row)) is not None),
            Decimal(0),
        )
    return result


class HandCalculatedBusinessOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deliveries = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.result = oracle(cls.deliveries)

    def test_exact_hand_calculated_results(self):
        self.assertEqual(self.result, EXPECTED)

    def test_every_source_row_and_parseable_amount_reconciles(self):
        r = self.result
        self.assertEqual(r["source_rows"], r["accepted_rows"] + r["quarantined_rows"] + r["excluded_rows"])
        self.assertEqual(r["received_deliveries"], r["source_rows"] + r["repeat_deliveries"])
        self.assertEqual(
            r["source_parseable_gbp"],
            r["net_recorded_gbp"] + r["quarantined_parseable_gbp"] + r["excluded_parseable_gbp"],
        )

    def test_identical_business_fields_at_distinct_original_rows_survive(self):
        first, second = self.deliveries[:2]
        self.assertEqual(
            {k: v for k, v in first.items() if k != "source_row"},
            {k: v for k, v in second.items() if k != "source_row"},
        )
        self.assertNotEqual(source_identity(first), source_identity(second))
        self.assertEqual(oracle([first, second])["net_recorded_gbp"], Decimal("40.00"))

    def test_repeated_delivery_does_not_add_a_sale(self):
        first = self.deliveries[0]
        result = oracle([first, first])
        self.assertEqual(result["source_rows"], 1)
        self.assertEqual(result["repeat_deliveries"], 1)
        self.assertEqual(result["net_recorded_gbp"], Decimal("20.00"))

    def test_unknown_customer_remains_in_sales_and_out_of_known_coverage(self):
        unknown = self.deliveries[2]
        result = oracle([unknown])
        self.assertEqual(result["net_recorded_gbp"], Decimal("7.50"))
        self.assertEqual(result["accepted_rows"], 1)
        self.assertEqual(result["known_customer_rows"], 0)

    def test_rounding_is_half_up_for_both_signs(self):
        self.assertEqual(_amount(self.deliveries[12]), Decimal("1.01"))
        self.assertEqual(_amount(self.deliveries[13]), Decimal("-1.01"))

    def test_cancellation_sign_is_not_invented(self):
        self.assertEqual(_disposition(self.deliveries[3]), "accepted")
        self.assertEqual(_disposition(self.deliveries[9]), "quarantined")
        self.assertEqual(_amount(self.deliveries[9]), Decimal("10.00"))

    def test_fractional_quantity_is_not_truncated_to_an_integer(self):
        self.assertEqual(_disposition(self.deliveries[15]), "quarantined")
        self.assertEqual(_amount(self.deliveries[15]), Decimal("1.50"))

    def test_conflicting_payload_is_not_silently_deduplicated(self):
        original = self.deliveries[0]
        correction_without_revision = {**original, "quantity": 999}
        with self.assertRaisesRegex(ValueError, "explicit revision"):
            oracle([original, correction_without_revision])


if __name__ == "__main__":
    unittest.main()
