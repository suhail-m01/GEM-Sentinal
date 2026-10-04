# Final Demo Run

From the project root on Windows PowerShell:

## First-time / fresh demo database
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open another terminal from project root:
```powershell
backend\.venv\Scripts\python.exe mock-govt-portals\main.py
```

Open another terminal:
```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## Razorpay Test Mode
Fill these in `backend/.env`, then restart the backend:
```env
ENABLE_RAZORPAY_PAYMENTS=true
RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_test_key_secret
RAZORPAY_WEBHOOK_SECRET=
```

The final six payment/sealing demo tenders are created by the main `python -m app.seed`; no separate extension seed is required.
