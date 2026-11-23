from datetime import datetime
from zoneinfo import ZoneInfo

from main import (
    Opportunity,
    deduplicate,
    read_pending_ids,
    read_state,
    reserve_state,
    write_state,
)


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


def test_reserved_ids_block_duplicate_deduplication(tmp_path):
    state_path = tmp_path / "posted_events.json"
    reserve_state(state_path, set(), {"event-1", "event-2"})
    assert read_pending_ids(state_path) == {"event-1", "event-2"}
    assert read_state(state_path) == set()

    event = make_event("event-1", "https://events.ieee.org/already", 50)
    assert deduplicate([event], read_state(state_path) | read_pending_ids(state_path)) == []


def test_final_state_moves_pending_to_posted(tmp_path):
    state_path = tmp_path / "posted_events.json"
    reserve_state(state_path, set(), {"event-1"})
    write_state(state_path, set(), {"event-1"}, [])
    assert read_pending_ids(state_path) == set()
    assert read_state(state_path) == {"event-1"}
