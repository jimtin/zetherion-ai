# OpenAPI Contracts

The authoritative OpenAPI specs for the new document intelligence and CGS wiring surface are stored as versioned YAML files:

- Zetherion upstream contract (internal): [`openapi-public-api.yaml`](openapi-public-api.yaml)
- CGS public/operator contract (external): [`openapi-cgs-gateway.yaml`](openapi-cgs-gateway.yaml)
  - Includes runtime conversation APIs, document intelligence APIs, tenant lifecycle APIs, tenant admin APIs, and tenant email admin control-plane APIs.

These files are part of the required endpoint documentation bundle and must be updated whenever route contracts change.

Contract parity gates:
- `scripts/check-route-doc-parity.py` validates Skills + upstream `/api/v1` docs parity.
- `scripts/check-cgs-route-doc-parity.py` validates CGS route registrations vs `openapi-cgs-gateway.yaml`.
