"""Write-path functions for GrindPoints: sign-up and redemption.

Takes whatever DB-API 2.0 connection the caller supplies (sqlite3 in the
tests, a Postgres driver in the deployed service).
"""

from __future__ import annotations


def sign_up_customer(conn, email: str, display_name: str) -> int:
    """Insert a new customer at the bronze tier with a zero points balance,
    and return the generated id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO customers (email, display_name, loyalty_tier, points_balance) "
        "VALUES (?, ?, 'bronze', 0)",
        (email, display_name),
    )
    conn.commit()
    return cursor.lastrowid


def record_redemption(conn, customer_id: int, reward_name: str, points_cost: int) -> None:
    """Record a redemption and deduct the spent points from the customer's
    running balance."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO redemptions (customer_id, reward_name, points_spent) VALUES (?, ?, ?)",
        (customer_id, reward_name, points_cost),
    )
    conn.commit()

    cursor.execute(
        "UPDATE customers SET points_balance = points_balance - ? WHERE id = ?",
        (points_cost, customer_id),
    )
    conn.commit()
