# Razorpay Tender Payment Integration

GeM-Sentinel uses Razorpay Standard Checkout in Test Mode for the SIH prototype.

## Current payment rule
- A universal **₹2,500 Application / Platform Maintenance Fee** applies to every bidder submission.
- EMD/Bid Security remains tender-specific.
- If EMD is payable, **one Razorpay order collects ₹2,500 + EMD together**.
- If EMD is not required or the bidder is exempt, the checkout collects **₹2,500 only**.
- Submission is blocked until the required checkout is verified.

## Security
- Order amount is calculated on the backend, never trusted from the browser.
- Razorpay secret stays in `backend/.env`.
- Checkout returns order/payment/signature values to the backend.
- Backend verifies the Razorpay HMAC-SHA256 signature before marking payment VERIFIED.
- Audit entries record order creation and successful verification.

## Demo examples
- `TDR-2026-012` Belagavi sealed: ₹2,500 + ₹1,50,000 EMD = **₹1,52,500** one checkout.
- `TDR-2026-015` Hubli unsealed: ₹2,500 + ₹50,000 EMD = **₹52,500** one checkout.
- `TDR-2026-013`: eligible Udyam bidder may be EMD-exempt, so **₹2,500** only.
- `TDR-2026-017`: no EMD, so **₹2,500** only.

Use your own Razorpay **Test Mode** credentials in `backend/.env`.
