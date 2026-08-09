"""Test of the redemption flow, against a real sqlite connection."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loyalty_writer import record_redemption, sign_up_customer


def _connect():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, display_name TEXT, "
        "loyalty_tier TEXT, points_balance INTEGER)"
    )
    conn.execute(
        "CREATE TABLE redemptions (id INTEGER PRIMARY KEY, customer_id INTEGER, "
        "reward_name TEXT, points_spent INTEGER)"
    )
    return conn


def test_redemption_deducts_points_and_records_the_ledger_row():
    conn = _connect()
    customer_id = sign_up_customer(conn, "ash@example.test", "Ash")
    conn.execute("UPDATE customers SET points_balance = 500 WHERE id = ?", (customer_id,))
    conn.commit()

    record_redemption(conn, customer_id, "free flat white", 150)

    balance = conn.execute(
        "SELECT points_balance FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()[0]
    assert balance == 350

    ledger_rows = conn.execute(
        "SELECT reward_name, points_spent FROM redemptions WHERE customer_id = ?", (customer_id,)
    ).fetchall()
    assert ledger_rows == [("free flat white", 150)]
