# Stack Profile: Python + FastAPI

**Status:** FIXTURE (test data only)
**Authored:** 2026-08-28 by the engineering-audit test suite
**Last refresh:** 2026-08-28, initial fixture
**Stack identifiers:** python, fastapi

**Trigger:** this stack profile applies when the codebase uses Python with the FastAPI web framework.

**Load this when:** a Python project using FastAPI as the primary web framework runs the audit.

Rules for Python + FastAPI stack follow.

---

### 1. Declare request and response models using Pydantic.

FastAPI uses Pydantic for schema validation and documentation. All request bodies and responses must be declared using Pydantic models or standard Python types, ensuring OpenAPI compatibility and automatic validation.

*Source: FastAPI documentation on request bodies. Rule id: SPFPY-R01. Volatility: durable. Verified: 2026-08-28 (fixture, not a real citation).*

### 2. Use async def for route handlers when I/O-bound operations are present.

FastAPI's async support allows handling concurrent requests efficiently. Handlers that perform I/O (database queries, external API calls) should be async to avoid blocking.

*Source: FastAPI concurrency documentation. Rule id: SPFPY-R02. Volatility: volatile. Verified: 2026-08-28 (fixture, not a real citation).*

### 3. Document all endpoints with docstrings and OpenAPI examples.

FastAPI automatically generates OpenAPI documentation from docstrings and examples. Every endpoint must include a docstring explaining its purpose and example request/response payloads for clarity.

*Source: FastAPI documentation generation. Rule id: SPFPY-R03. Volatility: durable. Verified: 2026-08-28 (fixture, not a real citation).*
