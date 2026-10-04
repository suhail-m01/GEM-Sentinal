# Combined Application Fee + EMD Payment

GeM-Sentinel now creates **one Razorpay order per tender payment step**.

- Universal Application / Platform Fee: **₹2,500**.
- If EMD/Bid Security is required and the bidder is not exempt, the checkout amount is **₹2,500 + EMD**.
- If EMD is not required, the checkout amount is **₹2,500**.
- If EMD exemption applies, the checkout amount is **₹2,500** and the EMD is shown as exempt.
- A verified `APPLICATION_FEE_EMD` payment satisfies both application-fee and EMD gates.
- Existing separately verified `APPLICATION_FEE` / `EMD` records remain recognised for backward compatibility.

Demo examples:
- `TDR-2026-012` (Belagavi, sealed): ₹2,500 + ₹1,50,000 EMD = **₹1,52,500 in one checkout**.
- `TDR-2026-015` (Hubli, unsealed): ₹2,500 + ₹50,000 EMD = **₹52,500 in one checkout**.
- `TDR-2026-013`: EMD exemption allowed; eligible Udyam bidder pays **₹2,500 only**.
- `TDR-2026-017`: no EMD; bidder pays **₹2,500 only**.
