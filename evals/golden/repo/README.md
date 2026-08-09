# GrindPoints

GrindPoints is a small loyalty analytics service for an independent cafe
chain. Customers earn points on every till transaction ("visit"), redeem
them for rewards, and move through bronze, silver and gold tiers. This
repository is a fictional, deliberately small slice of the service: a
schema, a write-path module, a couple of tests, a performance check and a
monthly report. It exists only as a fixture for the engineering-audit eval
harness and is not a real product.

## Layout

- `schema.sql`: the loyalty database schema.
- `loyalty_writer.py`: sign-up and redemption write paths.
- `tests/`: end-to-end tests of the write paths.
- `perf/latency_check.py`: an average-load check for the redemption endpoint.
- `reports/`: the monthly summary and the charts behind it.
