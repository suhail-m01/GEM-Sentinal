"""Registry of government source adapters (mock implementations today,
real authorized APIs can be swapped in here later)."""
from .base import SourceAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    a.source: a
    for a in [
        SourceAdapter("UDYAM", "udyam", "udyam"),
        SourceAdapter("GST", "gst", "gstin"),
        SourceAdapter("PAN", "pan", "pan"),
        SourceAdapter("DIGILOCKER", "digilocker", "pan"),
        SourceAdapter("MCA21", "mca21", "pan"),
        SourceAdapter("EPFO", "epfo", "pan"),
        SourceAdapter("ESIC", "esic", "pan"),
        SourceAdapter("STARTUP_INDIA", "startup_india", "pan"),
        SourceAdapter("NSIC", "nsic", "pan"),
        SourceAdapter("BIS", "bis", "pan"),
        SourceAdapter("BLACKLIST", "blacklist", "pan"),
        SourceAdapter("LOCAL_CONTENT", "local_content", "pan"),
    ]
}


def get_adapter(source: str) -> SourceAdapter:
    try:
        return ADAPTERS[source.upper()]
    except KeyError as exc:
        raise ValueError(f"Unknown source: {source}") from exc
