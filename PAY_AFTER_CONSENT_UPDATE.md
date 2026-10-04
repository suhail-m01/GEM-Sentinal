# Pay-after-consent application flow

The bidder application flow now uses payment as the final gate before submission.

1. Complete declaration (minimum 40 characters).
2. Complete/confirm compliance consent.
3. Complete the financial quotation and commercial declaration when the tender requires a financial bid.
4. Razorpay payment becomes enabled.
5. Pay the universal ₹2,500 application/platform fee plus EMD/Bid Security in the same checkout when EMD applies. If EMD is exempt/not required, only ₹2,500 is charged.
6. After backend Razorpay signature verification succeeds, Submit Application becomes enabled.
7. Backend submission still independently checks payment status and rejects unpaid submissions.

The Hubli/Belagavi sealed-vs-unsealed behavior and all existing EMD/exemption rules are unchanged.
