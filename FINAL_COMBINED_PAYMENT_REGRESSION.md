# Final Combined Payment Regression

## Change verified
- Universal application/platform fee: ₹2,500.
- Tender-specific EMD/Bid Security is added to the same Razorpay order when due.
- EMD exemption and no-EMD scenarios charge only ₹2,500.
- A verified `APPLICATION_FEE_EMD` record satisfies both submission gates.
- Backward compatibility recognizes older separately verified APPLICATION_FEE / EMD records.
- Belagavi sealed and Hubli unsealed jury-demo behavior remains unchanged.

## Validation performed
- Python AST syntax validation: 100 backend/test Python files, 0 errors.
- Frontend TypeScript: `tsc --noEmit`, 0 errors.
- Pytest collection: 109 tests.
- All 109 collected tests passed across isolated clean SQLite databases.
- New combined-payment tests cover:
  - ₹2,500 + fixed EMD in one order.
  - ₹2,500 only when no EMD is required.
  - ₹2,500 only when EMD exemption applies.
  - Razorpay HMAC verification and invalid-signature rejection.
- End-to-end regression covers payment-enabled tender through submission, sealed opening, AI/compliance verification, technical evaluation, financial evaluation, CFA award, contract, execution, delivery, settlement and audit closure.

## Note
Razorpay network checkout itself is not charged with real money during automated tests. Order creation is mocked while the application’s real amount calculation and HMAC signature verification code are exercised. Use your Razorpay Test Mode keys for interactive checkout.
