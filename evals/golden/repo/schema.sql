-- GrindPoints loyalty schema.
-- Postgres-flavoured DDL for the cafe loyalty analytics service.

CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    -- Optional: not every customer supplies a phone number at sign-up, and
    -- the sign-up form never requires one, so this stays nullable.
    phone TEXT,
    loyalty_tier TEXT NOT NULL DEFAULT 'bronze' CHECK (loyalty_tier IN ('bronze', 'silver', 'gold')),
    points_balance INTEGER NOT NULL DEFAULT 0,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE stores (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    suburb TEXT NOT NULL
);

-- A visit is one till transaction: a customer, a store, a time and an
-- amount, recorded by the till integration as each sale closes.
CREATE TABLE visits (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    store_id INTEGER NOT NULL REFERENCES stores(id),
    visited_at TIMESTAMPTZ NOT NULL,
    amount_spent NUMERIC(8,2) NOT NULL CHECK (amount_spent >= 0)
);

CREATE TABLE redemptions (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    reward_name TEXT NOT NULL,
    points_spent INTEGER,
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
