# GeM-Sentinel Final Regression Validation

This build was regression-tested after the Razorpay/tender/demo changes.

## Preserved demo behavior
- **Belagavi:** sealed-bid demonstration remains enabled.
- **Hubli:** unsealed/live-verification demonstration remains enabled.
- District procurement-officer access is now correctly scoped; a Hubli procurement officer cannot directly access Belagavi applications.

## Six added live jury-demo tenders
| Tender | District | Sealing | Application fee | EMD / Bid Security |
|---|---|---|---:|---:|
| TDR-2026-012 | Belagavi | Sealed | ₹1,000 | ₹1,50,000 fixed; exemption supported |
| TDR-2026-013 | Belagavi | Sealed | None | 2% of estimated value; exemption supported |
| TDR-2026-014 | Hubli | Unsealed | None | 1% of estimated value; exemption supported |
| TDR-2026-015 | Hubli | Unsealed | ₹2,500 | ₹50,000 fixed; exemption supported |
| TDR-2026-016 | Belagavi | Sealed | ₹2,500 | Not required |
| TDR-2026-017 | Hubli | Unsealed | None | Not required |

All six are published with future bid deadlines after a fresh seed.

## Payment-gateway checks
- Tender-specific payment requirement calculation.
- Razorpay order creation payload and paise conversion.
- Server-side HMAC-SHA256 signature verification.
- Invalid signature rejection.
- Payment audit record now references a materialized TenderPayment ID.
- Bid submission payment gate.
- EMD exemption path.

Razorpay network calls were tested with a mocked Razorpay order response because no account-specific test credentials were supplied to this environment. Insert your own `rzp_test_...` key and secret in `backend/.env` for live Test Mode checkout.

## End-to-end awarded tender scenario
`TDR-2026-012` + bidder `ramesh@bidder.in` was exercised through:

verified application fee -> EMD exemption -> financial quotation -> bid submission -> sealed access denial -> authorized bid opening -> AI verification -> compliance -> technical qualification -> financial evaluation -> CFA award -> contract -> execution -> delivery inspection -> payment settlement -> audit closure.

The test verified the pipeline ended with no current stage and all 11 stage certificates present.

## Passing regression suites executed
- Critical/security and jurisdiction isolation: **23 passed**
- Credibility: **5 passed**
- Government authentication: **7 passed**
- Financial bid: **6 passed**
- Pipeline + payment + final end-to-end: **11 passed**
- Watch/drift: **12 passed**
- Verification cache: **4 passed**
- V2 deadline + enhancements: **21 passed**
- V2 slice 1: **12 passed**
- Auction mode + reverse lifecycle + integrity/RBAC: **3 passed**
- Forward-auction lifecycle: **passed**
- Frontend TypeScript: `tsc --noEmit` **passed**
- Backend: `python -m compileall` **passed**

## Environment limitation
A full Next.js production build was attempted. TypeScript passed, but the sandbox could not download Next.js's Linux SWC binary because outbound npm/network access is unavailable. This is an environment dependency-download limitation, not a TypeScript compile failure.
