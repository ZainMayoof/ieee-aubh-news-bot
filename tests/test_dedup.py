from datetime import datetime
from zoneinfo import ZoneInfo

from main import Opportunity, deduplicate


BAHRAIN = ZoneInfo("Asia/Bahrain")


def make_event(event_id, url, score):
    return Opportunity(
        event_id=event_id,
        title="IEEE Student Event",
        url=url,
        start=datetime(2026, 12, 1, tzinfo=BAHRAIN),
        end=None,
        category="Workshop",
        summary="An official workshop for university students covering engineering topics.",
        online=True,
        student_value=True,
        score=score,
    )


def test_deduplicate_keeps_highest_score_per_url():
    events = [
        make_event("old-id", "https://events.ieee.org/workshop", 20),
        make_event("better-id", "https://events.ieee.org/workshop", 40),
    ]
    result = deduplicate(events, set())
    assert len(result) == 1
    assert result[0].event_id == "better-id"


def test_deduplicate_excludes_posted_ids():
    event = make_event("already-posted", "https://events.ieee.org/already", 50)
    assert deduplicate([event], {"already-posted"}) == []
