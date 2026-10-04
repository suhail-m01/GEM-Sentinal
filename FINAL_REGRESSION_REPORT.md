# GeM-Sentinel Final Regression Report

Date: 2026-10-04

## Final demo additions

Six additional published tenders are part of the main seed:

- TDR-2026-012 — Belagavi — sealed — application fee + fixed Bid Security
- TDR-2026-013 — Belagavi — sealed — no application fee + percentage Bid Security
- TDR-2026-014 — Hubli — unsealed — no application fee + percentage Bid Security
- TDR-2026-015 — Hubli — unsealed — application fee + fixed Bid Security
- TDR-2026-016 — Belagavi — sealed — application fee only
- TDR-2026-017 — Hubli — unsealed — no application fee / no Bid Security

The original jury split remains intact:

- TDR-2026-001 / Belagavi: sealed until authorized opening.
- TDR-2026-002 / Hubli: unsealed for live AI-verification demonstration.

## Payment coverage

Razorpay integration includes backend order creation, backend-owned amount calculation,
Standard Checkout integration, HMAC-SHA256 signature verification, payment persistence,
audit records, application-fee gate, EMD/Bid-Security gate, and EMD exemption handling.

Live money was not charged during automated regression. Razorpay network behavior is tested
with a mocked Razorpay order endpoint and the real application signature-verification logic.
Use your own Razorpay Test Mode Key ID + Secret in backend/.env for the jury demo.

## Automated regression result

All 105 collected pytest cases passed across isolated clean database runs.

Coverage includes:

- Authentication, token refresh, RBAC and bidder isolation
- Tender visibility and jurisdiction isolation
- Belagavi sealed / Hubli unsealed district demo
- Deadline and authorized bid opening behavior
- Deterministic compliance verification and credibility states
- Payment requirement matrix
- Razorpay order construction and HMAC verification
- Invalid Razorpay signature rejection
- Financial quote handling
- Reverse auction and forward auction lifecycle
- Live auction matrix / eligibility
- V2 workflow roles and tender-scoped assignments
- Pipeline, contract, execution, delivery, payment and audit stages
- Sentinel Watch and verification cache behavior
- Final payment-enabled tender end-to-end award flow

## End-to-end award scenario validated

Automated scenario: TDR-2026-012 + Ramesh bidder.

1. Payment obligation satisfied (verified application-fee record; eligible EMD exemption).
2. Financial quotation + tender application submitted.
3. Officer access blocked while Belagavi bid remained sealed.
4. Authorized bid opening performed.
5. AI/compliance verification executed.
6. Compliance finding recorded.
7. Technical qualification completed.
8. Financial evaluation passed.
9. CFA approval awarded the tender.
10. Contract issued.
11. Contract execution milestone recorded.
12. Delivery inspected and accepted.
13. Invoice/payment settlement recorded.
14. Audit closed.
15. Pipeline confirmed fully completed with award decision and generated certificates.

## Python compatibility

All 98 backend/test Python files were parsed using Python 3.11 grammar successfully.
This check specifically guards against the f-string issues discovered in earlier packages.

## Sandbox limitations

The execution sandbox did not have the project's complete pip/npm dependency set and outbound
package installation was unavailable. Backend regression was therefore executed with an
isolated SQLite database and temporary test-only compatibility shims for packages missing from
the sandbox (the shims are NOT included in this project). The actual project continues to use
the dependencies in backend/requirements.txt and PostgreSQL configuration for local execution.

Frontend source was not production-built in this final sandbox pass because node_modules could
not be installed without network access. No frontend dependency or source structure was replaced
for the test environment.
