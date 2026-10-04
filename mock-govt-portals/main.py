"""GeM-Sentinel — Mock Government Portal Service.

Simulates government source endpoints (Udyam, GST, PAN, DigiLocker, MCA21,
EPFO, ESIC, Startup India, NSIC, BIS, blacklist/debarment, local-content).

Supported simulation modes per (source, identifier):
  valid | expired | mismatch | not_found | unavailable | timeout | blacklist

Real authorized APIs can replace these endpoints later without touching
business logic (Adapter Pattern on the core side).
"""
import asyncio

import httpx  # noqa: F401  (kept for parity with real adapters)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Government Portals", version="1.0.0")

# ---------------------------------------------------------------------------
# Records (seeded demo data — clearly simulated, not official)
# ---------------------------------------------------------------------------

RECORDS: dict[str, dict] = {
    "gst": {
        # Ramesh — fully compliant
        "29ABRCT1234F1Z5": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "gstin": "29ABRCT1234F1Z5",
                "legal_name": "RAMESH TRADERS PVT LTD",
                "trade_name": "Ramesh Traders",
                "address": "12, Industrial Layout, Belagavi, Karnataka 590016",
                "registration_date": "2015-04-12",
                "filing_status": "CURRENT",
                "last_return_date": "2026-08-20",
                "turnover": 45000000,
                "authorized_rep": "Ramesh Kumar",
                "reference_id": "GST29ABRCT1234",
                "fields": {"legal_name": "RAMESH TRADERS PVT LTD", "gstin": "29ABRCT1234F1Z5", "issue_date": "12/04/2015", "issuer": "GSTN"},
            },
            "message": "GST registration active; filings current",
        },
        # Sita — registration active but filings lapsed (Scenario B)
        "29AAAPS5678K1Z2": {
            "status": "EXPIRED",
            "data": {
                "status": "SUSPENDED",
                "gstin": "29AAAPS5678K1Z2",
                "legal_name": "SITA ENGINEERING WORKS",
                "trade_name": "Sita Engineering",
                "address": "7, Ring Road, Belagavi, Karnataka 590009",
                "registration_date": "2017-09-01",
                "filing_status": "DEFAULTED",
                "last_return_date": "2024-03-15",
                "turnover": 25000000,
                "authorized_rep": "Sita Nair",
                "reference_id": "GST29AAAPS5678",
                "expiry_date": "2025-06-30",
                "fields": {"legal_name": "SITA ENGINEERING WORKS", "gstin": "29AAAPS5678K1Z2", "issue_date": "01/09/2017", "issuer": "GSTN"},
            },
            "message": "GST compliance lapsed — last return 2024-03-15 (filing defaulted)",
        },
        # Vikram — active (but debarred via blacklist)
        "29AAVCV9012L1Z7": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "gstin": "29AAVCV9012L1Z7",
                "legal_name": "VIKRAM INDUSTRIAL SUPPLIES",
                "trade_name": "Vikram Supplies",
                "address": "22, MIDC Phase II, Belagavi, Karnataka 590016",
                "registration_date": "2013-02-18",
                "filing_status": "CURRENT",
                "last_return_date": "2026-08-18",
                "turnover": 61000000,
                "authorized_rep": "Vikram Rao",
                "reference_id": "GST29AAVCV9012",
                "fields": {"legal_name": "VIKRAM INDUSTRIAL SUPPLIES", "gstin": "29AAVCV9012L1Z7", "issue_date": "18/02/2013", "issuer": "GSTN"},
            },
            "message": "GST registration active",
        },
        # Meena — active
        "29AABPM3456M1Z3": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "gstin": "29AABPM3456M1Z3",
                "legal_name": "MEENA POLYFAB",
                "trade_name": "Meena Polyfab",
                "address": "3, Kankanady, Belagavi, Karnataka 591142",
                "registration_date": "2019-11-05",
                "filing_status": "CURRENT",
                "last_return_date": "2026-08-22",
                "turnover": 18000000,
                "authorized_rep": "Meena Patil",
                "reference_id": "GST29AABPM3456",
                "fields": {"legal_name": "MEENA POLYFAB", "gstin": "29AABPM3456M1Z3", "issue_date": "05/11/2019", "issuer": "GSTN"},
            },
            "message": "GST registration active; filings current",
        },
        # --- RA demo bidders (TDR-2026-010) ---
        # A — Arjun Industrial Systems: fully compliant
        "29AAISA1001H1Z1": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE", "gstin": "29AAISA1001H1Z1",
                "legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "trade_name": "Arjun Industrial",
                "address": "15, GIDC Estate, Belagavi, Karnataka 590016",
                "registration_date": "2014-06-20", "filing_status": "CURRENT",
                "last_return_date": "2026-08-19", "turnover": 120000000,
                "authorized_rep": "Arjun Mehta", "reference_id": "GST29AAISA1001",
                "fields": {"legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "gstin": "29AAISA1001H1Z1", "issue_date": "20/06/2014", "issuer": "GSTN"},
            },
            "message": "GST registration active; filings current",
        },
        # B — Bharat Process Solutions: fully compliant
        "29AABPS2002J1Z2": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE", "gstin": "29AABPS2002J1Z2",
                "legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD", "trade_name": "Bharat Process",
                "address": "42, PE-4 Industrial Area, Belagavi, Karnataka 590018",
                "registration_date": "2012-03-08", "filing_status": "CURRENT",
                "last_return_date": "2026-08-21", "turnover": 90000000,
                "authorized_rep": "Bharat Gokhale", "reference_id": "GST29AABPS2002",
                "fields": {"legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD", "gstin": "29AABPS2002J1Z2", "issue_date": "08/03/2012", "issuer": "GSTN"},
            },
            "message": "GST registration active; filings current",
        },
        # C — Chetan Equipments: GST lapsed (the RA-demo compliance FAIL)
        "29AACEQ3003K1Z3": {
            "status": "EXPIRED",
            "data": {
                "status": "SUSPENDED", "gstin": "29AACEQ3003K1Z3",
                "legal_name": "CHETAN EQUIPMENTS PVT LTD", "trade_name": "Chetan Equipments",
                "address": "8, Madhyamlok, Belagavi, Karnataka 590009",
                "registration_date": "2016-08-14", "filing_status": "DEFAULTED",
                "last_return_date": "2025-01-20", "turnover": 110000000,
                "authorized_rep": "Chetan Kulkarni", "reference_id": "GST29AACEQ3003",
                "expiry_date": "2025-12-31",
                "fields": {"legal_name": "CHETAN EQUIPMENTS PVT LTD", "gstin": "29AACEQ3003K1Z3", "issue_date": "14/08/2016", "issuer": "GSTN"},
            },
            "message": "GST compliance lapsed — last return 2025-01-20 (filing defaulted)",
        },
        # D — Delta Plant & Machinery: fully compliant
        "29AADPM4004L1Z4": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE", "gstin": "29AADPM4004L1Z4",
                "legal_name": "DELTA PLANT & MACHINERY PVT LTD", "trade_name": "Delta Plant",
                "address": "27, Industrial Layout, Belagavi, Karnataka 590016",
                "registration_date": "2011-02-25", "filing_status": "CURRENT",
                "last_return_date": "2026-08-18", "turnover": 85000000,
                "authorized_rep": "Deepak Iyer", "reference_id": "GST29AADPM4004",
                "fields": {"legal_name": "DELTA PLANT & MACHINERY PVT LTD", "gstin": "29AADPM4004L1Z4", "issue_date": "25/02/2011", "issuer": "GSTN"},
            },
            "message": "GST registration active; filings current",
        },
    },
    "udyam": {
        "U1234KA2601A0012345": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "udyam_number": "U1234KA2601A0012345",
                "legal_name": "RAMESH TRADERS PVT LTD",
                "category": "MSME",
                "issue_date": "2023-06-01",
                "reference_id": "UDYAM-12345",
                "fields": {"legal_name": "RAMESH TRADERS PVT LTD", "udyam_number": "U1234KA2601A0012345", "issue_date": "01/06/2023", "issuer": "Udyam/MSME"},
            },
            "message": "Udyam registration active",
        },
        # Sita — legal name mismatch at source (Scenario B)
        "U5678KA2502B0056789": {
            "status": "MISMATCH",
            "data": {
                "status": "ACTIVE",
                "udyam_number": "U5678KA2502B005678X",
                "legal_name": "SITA ENGINEERING",
                "category": "MSME",
                "issue_date": "2022-10-12",
                "reference_id": "UDYAM-56789",
                "fields": {"legal_name": "SITA ENGINEERING", "udyam_number": "U5678KA2502B005678X", "issue_date": "12/10/2022", "issuer": "Udyam/MSME"},
            },
            "message": "Name mismatch: source legal name 'SITA ENGINEERING' differs from submitted details",
        },
        "U9012KA2403C0090123": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "udyam_number": "U9012KA2403C0090123",
                "legal_name": "VIKRAM INDUSTRIAL SUPPLIES",
                "category": "MSME",
                "issue_date": "2023-01-20",
                "reference_id": "UDYAM-90123",
                "fields": {"legal_name": "VIKRAM INDUSTRIAL SUPPLIES", "udyam_number": "U9012KA2403C0090123", "issue_date": "20/01/2023", "issuer": "Udyam/MSME"},
            },
            "message": "Udyam registration active",
        },
        "U3456KA2504D0034567": {
            "status": "VALID",
            "data": {
                "status": "ACTIVE",
                "udyam_number": "U3456KA2504D0034567",
                "legal_name": "MEENA POLYFAB",
                "category": "MSME",
                "issue_date": "2024-02-11",
                "reference_id": "UDYAM-34567",
                "fields": {"legal_name": "MEENA POLYFAB", "udyam_number": "U3456KA2504D0034567", "issue_date": "11/02/2024", "issuer": "Udyam/MSME"},
            },
            "message": "Udyam registration active",
        },
        "U1001KA2605A0010001": {
            "status": "VALID",
            "data": {"status": "ACTIVE", "udyam_number": "U1001KA2605A0010001",
                     "legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "category": "MSME",
                     "issue_date": "2023-08-15", "reference_id": "UDYAM-10001",
                     "fields": {"legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "udyam_number": "U1001KA2605A0010001", "issue_date": "15/08/2023", "issuer": "Udyam/MSME"}},
            "message": "Udyam registration active",
        },
        "U2002KA2606B0020002": {
            "status": "VALID",
            "data": {"status": "ACTIVE", "udyam_number": "U2002KA2606B0020002",
                     "legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD", "category": "MSME",
                     "issue_date": "2022-12-02", "reference_id": "UDYAM-20002",
                     "fields": {"legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD", "udyam_number": "U2002KA2606B0020002", "issue_date": "02/12/2022", "issuer": "Udyam/MSME"}},
            "message": "Udyam registration active",
        },
        "U3003KA2607C0030003": {
            "status": "VALID",
            "data": {"status": "ACTIVE", "udyam_number": "U3003KA2607C0030003",
                     "legal_name": "CHETAN EQUIPMENTS PVT LTD", "category": "MSME",
                     "issue_date": "2023-03-19", "reference_id": "UDYAM-30003",
                     "fields": {"legal_name": "CHETAN EQUIPMENTS PVT LTD", "udyam_number": "U3003KA2607C0030003", "issue_date": "19/03/2023", "issuer": "Udyam/MSME"}},
            "message": "Udyam registration active",
        },
        "U4004KA2608D0040004": {
            "status": "VALID",
            "data": {"status": "ACTIVE", "udyam_number": "U4004KA2608D0040004",
                     "legal_name": "DELTA PLANT & MACHINERY PVT LTD", "category": "MSME",
                     "issue_date": "2024-01-30", "reference_id": "UDYAM-40004",
                     "fields": {"legal_name": "DELTA PLANT & MACHINERY PVT LTD", "udyam_number": "U4004KA2608D0040004", "issue_date": "30/01/2024", "issuer": "Udyam/MSME"}},
            "message": "Udyam registration active",
        },
    },
    "pan": {
        "ABRCT1234F": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "ABRCT1234F", "name": "RAMESH TRADERS PVT LTD", "reference_id": "PAN-ABRCT1234F", "fields": {"pan": "ABRCT1234F", "legal_name": "RAMESH TRADERS PVT LTD"}}, "message": "PAN record active"},
        "AAAPS5678K": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AAAPS5678K", "name": "SITA ENGINEERING WORKS", "reference_id": "PAN-AAAPS5678K", "fields": {"pan": "AAAPS5678K", "legal_name": "SITA ENGINEERING WORKS"}}, "message": "PAN record active"},
        "AAVCV9012L": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AAVCV9012L", "name": "VIKRAM INDUSTRIAL SUPPLIES", "reference_id": "PAN-AAVCV9012L", "fields": {"pan": "AAVCV9012L", "legal_name": "VIKRAM INDUSTRIAL SUPPLIES"}}, "message": "PAN record active"},
        "AABPM3456M": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AABPM3456M", "name": "MEENA POLYFAB", "reference_id": "PAN-AABPM3456M", "fields": {"pan": "AABPM3456M", "legal_name": "MEENA POLYFAB"}}, "message": "PAN record active"},
        "AAISA1001H": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AAISA1001H", "name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "reference_id": "PAN-AAISA1001H", "fields": {"pan": "AAISA1001H", "legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD"}}, "message": "PAN record active"},
        "AABPS2002J": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AABPS2002J", "name": "BHARAT PROCESS SOLUTIONS PVT LTD", "reference_id": "PAN-AABPS2002J", "fields": {"pan": "AABPS2002J", "legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD"}}, "message": "PAN record active"},
        "AACEQ3003K": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AACEQ3003K", "name": "CHETAN EQUIPMENTS PVT LTD", "reference_id": "PAN-AACEQ3003K", "fields": {"pan": "AACEQ3003K", "legal_name": "CHETAN EQUIPMENTS PVT LTD"}}, "message": "PAN record active"},
        "AADPM4004L": {"status": "VALID", "data": {"status": "ACTIVE", "pan": "AADPM4004L", "name": "DELTA PLANT & MACHINERY PVT LTD", "reference_id": "PAN-AADPM4004L", "fields": {"pan": "AADPM4004L", "legal_name": "DELTA PLANT & MACHINERY PVT LTD"}}, "message": "PAN record active"},
    },
    "epfo": {
        "ABRCT1234F": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/00123", "compliance": "CURRENT", "reference_id": "EPFO-00123", "fields": {"issuer": "EPFO", "issue_date": "01/04/2016"}}, "message": "EPFO establishment active and current"},
        "AAAPS5678K": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/00567", "compliance": "CURRENT", "reference_id": "EPFO-00567", "fields": {"issuer": "EPFO", "issue_date": "15/06/2018"}}, "message": "EPFO establishment active and current"},
        "AAVCV9012L": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/00901", "compliance": "CURRENT", "reference_id": "EPFO-00901", "fields": {"issuer": "EPFO", "issue_date": "01/01/2014"}}, "message": "EPFO establishment active and current"},
        # Meena — Scenario D: portal timeout simulated (fault default below)
        "AABPM3456M": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/00345", "compliance": "CURRENT", "reference_id": "EPFO-00345", "fields": {"issuer": "EPFO", "issue_date": "01/12/2020"}}, "message": "EPFO establishment active and current"},
        "AAISA1001H": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/10001", "compliance": "CURRENT", "reference_id": "EPFO-10001", "fields": {"issuer": "EPFO", "issue_date": "01/07/2015"}}, "message": "EPFO establishment active and current"},
        "AABPS2002J": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/20002", "compliance": "CURRENT", "reference_id": "EPFO-20002", "fields": {"issuer": "EPFO", "issue_date": "15/04/2013"}}, "message": "EPFO establishment active and current"},
        "AACEQ3003K": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/30003", "compliance": "CURRENT", "reference_id": "EPFO-30003", "fields": {"issuer": "EPFO", "issue_date": "20/09/2017"}}, "message": "EPFO establishment active and current"},
        "AADPM4004L": {"status": "VALID", "data": {"status": "ACTIVE", "establishment": "EPFO/KLN/BEL/40004", "compliance": "CURRENT", "reference_id": "EPFO-40004", "fields": {"issuer": "EPFO", "issue_date": "10/03/2012"}}, "message": "EPFO establishment active and current"},
    },
    "esic": {
        k: {"status": "VALID", "data": {"status": "ACTIVE", "compliance": "CURRENT", "reference_id": f"ESIC-{k[-3:]}", "fields": {"issuer": "ESIC", "issue_date": "01/04/2016"}}, "message": "ESIC registration current"}
        for k in ("ABRCT1234F", "AAAPS5678K", "AAVCV9012L", "AABPM3456M",
               "AAISA1001H", "AABPS2002J", "AACEQ3003K", "AADPM4004L")
    },
    "mca21": {
        "ABRCT1234F": {"status": "VALID", "data": {"status": "ACTIVE", "cin": "CIN-U24230KA2015PTC0012345", "name": "RAMESH TRADERS PVT LTD", "incorporation_date": "2015-04-12", "reference_id": "MCA-0012345", "fields": {"legal_name": "RAMESH TRADERS PVT LTD", "certificate_number": "CIN-U24230KA2015PTC0012345"}}, "message": "MCA21 company active"},
        "AAAPS5678K": {"status": "NOT_FOUND", "data": {}, "message": "Proprietorship — no MCA21 incorporation record (expected)"},
        "AAVCV9012L": {"status": "NOT_FOUND", "data": {}, "message": "Partnership — no MCA21 incorporation record"},
        "AABPM3456M": {"status": "NOT_FOUND", "data": {}, "message": "Proprietorship — no MCA21 incorporation record (expected)"},
        "AAISA1001H": {"status": "VALID", "data": {"status": "ACTIVE", "cin": "CIN-U28210KA2014PTC0010001", "name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "incorporation_date": "2014-06-20", "reference_id": "MCA-0010001", "fields": {"legal_name": "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "certificate_number": "CIN-U28210KA2014PTC0010001"}}, "message": "MCA21 company active"},
        "AABPS2002J": {"status": "VALID", "data": {"status": "ACTIVE", "cin": "CIN-U28210KA2012PTC0020002", "name": "BHARAT PROCESS SOLUTIONS PVT LTD", "incorporation_date": "2012-03-08", "reference_id": "MCA-0020002", "fields": {"legal_name": "BHARAT PROCESS SOLUTIONS PVT LTD", "certificate_number": "CIN-U28210KA2012PTC0020002"}}, "message": "MCA21 company active"},
        "AACEQ3003K": {"status": "VALID", "data": {"status": "ACTIVE", "cin": "CIN-U28210KA2016PTC0030003", "name": "CHETAN EQUIPMENTS PVT LTD", "incorporation_date": "2016-08-14", "reference_id": "MCA-0030003", "fields": {"legal_name": "CHETAN EQUIPMENTS PVT LTD", "certificate_number": "CIN-U28210KA2016PTC0030003"}}, "message": "MCA21 company active"},
        "AADPM4004L": {"status": "VALID", "data": {"status": "ACTIVE", "cin": "CIN-U28210KA2011PTC0040004", "name": "DELTA PLANT & MACHINERY PVT LTD", "incorporation_date": "2011-02-25", "reference_id": "MCA-0040004", "fields": {"legal_name": "DELTA PLANT & MACHINERY PVT LTD", "certificate_number": "CIN-U28210KA2011PTC0040004"}}, "message": "MCA21 company active"},
    },
    "digilocker": {
        k: {"status": "VALID", "data": {"status": "ACTIVE", "documents_available": 3, "reference_id": f"DL-{k[-4:]}", "fields": {}}, "message": "DigiLocker vault accessible"}
        for k in ("ABRCT1234F", "AAAPS5678K", "AAVCV9012L", "AABPM3456M",
               "AAISA1001H", "AABPS2002J", "AACEQ3003K", "AADPM4004L")
    },
    "startup_india": {
        "ABRCT1234F": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition (established enterprise)"},
        "AAAPS5678K": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition"},
        "AAVCV9012L": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition"},
        "AABPM3456M": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition"},
        "AAISA1001H": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition (established enterprise)"},
        "AABPS2002J": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition (established enterprise)"},
        "AACEQ3003K": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition (established enterprise)"},
        "AADPM4004L": {"status": "NOT_FOUND", "data": {}, "message": "No Startup India recognition (established enterprise)"},
    },
    "nsic": {
        k: {"status": "NOT_FOUND", "data": {}, "message": "No NSIC registration on record"}
        for k in ("ABRCT1234F", "AAAPS5678K", "AAVCV9012L", "AABPM3456M",
               "AAISA1001H", "AABPS2002J", "AACEQ3003K", "AADPM4004L")
    },
    "bis": {
        "ABRCT1234F": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
        "AAAPS5678K": {"status": "VALID", "data": {"status": "ACTIVE", "license": "BIS/ISO9001/KLN/4431", "reference_id": "BIS-4431", "fields": {"issuer": "BIS", "certificate_number": "BIS/ISO9001/KLN/4431"}}, "message": "BIS ISO 9001 license active"},
        "AAVCV9012L": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
        "AABPM3456M": {"status": "VALID", "data": {"status": "ACTIVE", "license": "BIS/ISO14001/KLN/5522", "reference_id": "BIS-5522", "fields": {"issuer": "BIS", "certificate_number": "BIS/ISO14001/KLN/5522"}}, "message": "BIS ISO 14001 license active"},
        "AAISA1001H": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
        "AABPS2002J": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
        "AACEQ3003K": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
        "AADPM4004L": {"status": "NOT_FOUND", "data": {}, "message": "No BIS certification on record"},
    },
    "local_content": {
        "ABRCT1234F": {"status": "VALID", "data": {"percent": 45, "reference_id": "LC-29-00123", "fields": {}}, "message": "Declared local content 45%"},
        "AAAPS5678K": {"status": "VALID", "data": {"percent": 55, "reference_id": "LC-29-00567", "fields": {}}, "message": "Declared local content 55%"},
        "AAVCV9012L": {"status": "VALID", "data": {"percent": 35, "reference_id": "LC-29-00901", "fields": {}}, "message": "Declared local content 35%"},
        "AABPM3456M": {"status": "VALID", "data": {"percent": 40, "reference_id": "LC-29-00345", "fields": {}}, "message": "Declared local content 40%"},
        "AAISA1001H": {"status": "VALID", "data": {"percent": 48, "reference_id": "LC-29-10001", "fields": {}}, "message": "Declared local content 48%"},
        "AABPS2002J": {"status": "VALID", "data": {"percent": 42, "reference_id": "LC-29-20002", "fields": {}}, "message": "Declared local content 42%"},
        "AACEQ3003K": {"status": "VALID", "data": {"percent": 38, "reference_id": "LC-29-30003", "fields": {}}, "message": "Declared local content 38%"},
        "AADPM4004L": {"status": "VALID", "data": {"percent": 45, "reference_id": "LC-29-40004", "fields": {}}, "message": "Declared local content 45%"},
    },
    "blacklist": {
        # Scenario C: Vikram debarred
        "AAVCV9012L": {
            "status": "BLACKLIST",
            "data": {
                "entity_name": "VIKRAM INDUSTRIAL SUPPLIES",
                "pan": "AAVCV9012L",
                "gstin": "29AAVCV9012L1Z7",
                "reason": "Supply of sub-standard industrial lubricants; debarment under CPCL purchase code clause 2.4.7",
                "source_authority": "CPCL Procurement / CPSE Vigilance",
                "order_ref": "CPCL/DEB/2025/117",
                "valid_from": "2025-03-01",
                "valid_until": "2027-03-01",
            },
            "message": "DEBARMENT RECORD FOUND — CPCL/DEB/2025/117",
        },
        "ABRCT1234F": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AAAPS5678K": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AABPM3456M": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AAISA1001H": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AABPS2002J": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AACEQ3003K": {"status": "VALID", "data": {}, "message": "No debarment record"},
        "AADPM4004L": {"status": "VALID", "data": {}, "message": "No debarment record"},
    },
}

# Fault injection: key = "source" or "source:identifier"
FAULTS: dict[str, str] = {
    # Scenario D default: EPFO portal times out for Meena's PAN
    "epfo:AABPM3456M": "timeout",
}

# Call counters (test/ops visibility into how often each source is hit)
CALL_COUNTS: dict[str, int] = {}

TIMEOUT_SECONDS = 4.0


class VerifyIn(BaseModel):
    identifier: str = ""
    gstin: str | None = None
    company_name: str | None = None


class FaultIn(BaseModel):
    source: str
    mode: str  # valid | expired | mismatch | not_found | unavailable | timeout | blacklist
    identifier: str | None = None


# ---------------------------------------------------------------------------
# Value-level state override (Sentinel Watch demo beat).
# Unlike fault injection (which simulates OUTAGE MODES), this changes the
# VALUE a source returns — e.g. flipping a GST registration from ACTIVE to
# CANCELLED after technical qualification — so continuous monitoring can
# detect real-world drift, not just outages.
# ---------------------------------------------------------------------------
STATE_OVERRIDES: dict[str, dict] = {}  # key: "{source}:{identifier}"


class StateIn(BaseModel):
    identifier: str
    status: str | None = None          # outer record status, e.g. VALID
    inner_status: str | None = None    # data.status, e.g. ACTIVE / CANCELLED
    message: str | None = None


def _state_key(source: str, identifier: str) -> str:
    return f"{source.strip().lower()}:{(identifier or '').strip()}"


@app.post("/api/admin/state")
def set_state(source: str, body: StateIn):
    """Override the VALUE a (source, identifier) record returns (simulated)."""
    records = RECORDS.get(source.strip().lower())
    if records is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {source}")
    ident = (body.identifier or "").strip()
    if ident not in records:
        raise HTTPException(status_code=404, detail=f"No record for identifier {ident}")
    rec = records[ident]
    key = _state_key(source, ident)
    # Snapshot the PRISTINE record before mutating — clear_state restores
    # from this, so it must be the seeded value, not the flipped one.
    if key not in STATE_OVERRIDES:
        import copy as _copy
        STATE_OVERRIDES[key] = {"_original": _copy.deepcopy(records[ident])}
    if body.status:
        rec["status"] = body.status
    if body.inner_status:
        rec.setdefault("data", {})["status"] = body.inner_status
    if body.message:
        rec["message"] = body.message
    STATE_OVERRIDES[key].update({"status": rec.get("status"),
                                 "inner_status": rec.get("data", {}).get("status")})
    public = {k: v for k, v in STATE_OVERRIDES[key].items() if k != "_original"}
    return {"ok": True, "key": key, "now": public,
            "note": "SIMULATED state change — mock portal only"}


@app.delete("/api/admin/state/{source}/{identifier}")
def clear_state(source: str, identifier: str):
    """Restore a source record to its seeded value (simulated)."""
    key = _state_key(source, identifier)
    if key not in STATE_OVERRIDES:
        raise HTTPException(status_code=404, detail="No override for this record")
    src = source.strip().lower()
    ident = identifier.strip()
    original = STATE_OVERRIDES[key].pop("_original", None)
    if original is not None and src in RECORDS:
        import copy as _copy
        RECORDS[src][ident] = _copy.deepcopy(original)
    del STATE_OVERRIDES[key]
    return {"ok": True, "key": key, "note": "restored to seeded (SIMULATED) value"}


@app.get("/api/admin/state")
def list_state():
    return {"overrides": STATE_OVERRIDES}


@app.get("/health")
def health():
    return {"service": "mock-govt-portals", "status": "ok"}


@app.get("/api/sources")
def sources():
    return {"sources": list(RECORDS.keys()), "faults": FAULTS}


def _fault_mode(source: str, identifier: str) -> str | None:
    if f"{source}:{identifier}" in FAULTS:
        return FAULTS[f"{source}:{identifier}"]
    if source in FAULTS:
        return FAULTS[source]
    return None


@app.post("/api/{source}/verify")
async def verify(source: str, body: VerifyIn):
    if source not in RECORDS:
        raise HTTPException(status_code=404, detail=f"Unknown source {source}")
    CALL_COUNTS[source] = CALL_COUNTS.get(source, 0) + 1
    records = RECORDS[source]
    identifier = (body.identifier or body.gstin or "").strip()

    mode = _fault_mode(source, identifier)
    if mode == "timeout":
        await asyncio.sleep(TIMEOUT_SECONDS)
        return {"status": "UNAVAILABLE", "message": "Source timed out", "data": {}}
    if mode == "unavailable":
        return {"status": "UNAVAILABLE", "message": "Source temporarily unavailable (simulated)", "data": {}}
    if mode == "not_found":
        return {"status": "NOT_FOUND", "message": "Record not found (simulated)", "data": {}}
    if mode == "expired":
        return {"status": "EXPIRED", "message": "Record expired (simulated)", "data": {}}
    if mode == "mismatch":
        return {"status": "MISMATCH", "message": "Record mismatch (simulated)", "data": {}}
    if mode == "blacklist":
        return {
            "status": "BLACKLIST",
            "message": "DEBARMENT RECORD FOUND (simulated)",
            "data": {"entity_name": "SIMULATED ENTITY", "reason": "simulated debarment",
                     "source_authority": "Simulated Authority", "order_ref": "SIM-000"},
        }

    rec = records.get(identifier)
    if rec is None:
        # blacklist accepts gstin/company fallback
        if source == "blacklist" and body.gstin:
            for k, v in records.items():
                if v.get("data", {}).get("gstin") == body.gstin:
                    return v
        return {"status": "NOT_FOUND", "message": "Record not found at source", "data": {}}
    return rec


@app.post("/api/admin/fault")
def set_fault(body: FaultIn):
    global FAULTS
    if body.mode not in ("valid", "expired", "mismatch", "not_found", "unavailable", "timeout", "blacklist"):
        raise HTTPException(status_code=400, detail="Invalid mode")
    if body.identifier:
        key = f"{body.source}:{body.identifier}"
        FAULTS[key] = body.mode
    else:
        # clear per-identifier, set source-wide (valid clears)
        FAULTS = {k: v for k, v in FAULTS.items() if not k.startswith(body.source + ":")}
        if body.mode != "valid":
            FAULTS[body.source] = body.mode
    return {"faults": FAULTS}


@app.delete("/api/admin/fault/{source}")
def clear_fault(source: str):
    global FAULTS
    FAULTS = {k: v for k, v in FAULTS.items() if not (k == source or k.startswith(source + ":"))}
    return {"faults": FAULTS}


@app.get("/api/admin/fault")
def list_faults():
    return {"faults": FAULTS}


@app.get("/api/admin/stats")
def stats():
    """Per-source verify call counts (resets on restart; test/ops helper)."""
    return {"calls": dict(CALL_COUNTS), "total": sum(CALL_COUNTS.values())}


@app.post("/api/admin/stats/reset")
def reset_stats():
    CALL_COUNTS.clear()
    return {"calls": {}, "total": 0}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
