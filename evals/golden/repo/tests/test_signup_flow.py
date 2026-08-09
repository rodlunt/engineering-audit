"""Test of the signup flow, against a real sqlite connection."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loyalty_writer import sign_up_customer


def _connect():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, display_name TEXT, "
        "loyalty_tier TEXT, points_balance INTEGER)"
    )
    return conn


def test_signup_creates_a_bronze_tier_customer_with_zero_balance():
    conn = _connect()

    customer_id = sign_up_customer(conn, "priya@example.test", "Priya")

    row = conn.execute(
        "SELECT loyalty_tier, points_balance FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    assert row == ("bronze", 0)
