"""Topic names: what the CLI accepts → what the server expects. Standard library only."""

TOPICS = {
    "money": "money",
    "climate": "climate_energy",
    "humanitarian": "humanitarian",
}


def server_topic(name: str) -> str:
    key = (name or "").strip().lower()
    if key in TOPICS:
        return TOPICS[key]
    if key in TOPICS.values():
        return key
    raise ValueError(f"Unknown topic {name!r}. Choose one of: {', '.join(TOPICS)}.")


def page_offset(page: int, limit: int) -> int:
    """--page N (1-based) → offset."""
    if page < 1:
        raise ValueError("--page starts at 1.")
    return (page - 1) * limit
