"""AI engine: document extraction + explanations.

AI ONLY EXPLAINS. It never changes deterministic rule results, scores, or
eligibility. When GEMINI_API_KEY is unavailable, a deterministic fallback
explainer/extractor is used (per specification).
"""
from .explainer import explain_verification  # noqa: F401
from .extractor import extract_document_fields, extraction_engine  # noqa: F401
