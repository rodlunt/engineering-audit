# Stack Profile: Python + Django

**Status:** FIXTURE (test data only)
**Authored:** 2026-08-28 by the engineering-audit test suite
**Last refresh:** 2026-08-28, initial fixture
**Stack identifiers:** python, django

**Trigger:** this stack profile applies when the codebase uses Python with the Django web framework.

**Load this when:** a Python project using Django as the primary web framework runs the audit.

Rules for Python + Django stack follow.

---

### 1. Define all model fields with explicit verbose_name and help_text.

Django ORM fields should include human-readable labels and help text for admin interface clarity and documentation.

*Source: Django model field documentation. Rule id: SPDPY-R01. Volatility: durable. Verified: 2026-08-28 (fixture, not a real citation).*

### 2. Use Django's ORM querysets instead of raw SQL where possible.

The ORM provides automatic parameterization and SQL injection protection. Raw SQL should only be used when the ORM cannot express the query.

*Source: Django ORM security guide. Rule id: SPDPY-R02. Volatility: durable. Verified: 2026-08-28 (fixture, not a real citation).*
