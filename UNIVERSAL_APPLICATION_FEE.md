# Universal Application / Platform Maintenance Fee

GeM-Sentinel now requires a non-refundable application/platform maintenance fee from every bidder submitting an application through the platform, independent of EMD/Bid Security.

- Universal demo fee: **₹2,500 per application**
- Configure with `APPLICATION_FEE_AMOUNT` in `backend/.env`.
- Razorpay Test Mode handles the payment.
- Application submission is blocked until the fee is verified.
- EMD/Bid Security remains tender-specific and can still be required, waived, or exempted separately.
- Existing Hubli/Belagavi sealing behavior is unchanged.
