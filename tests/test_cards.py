import json
from datetime import datetime
from zoneinfo import ZoneInfo

from main import Opportunity, card_payload


BAHRAIN = ZoneInfo("Asia/Bahrain")


def test_card_payload_shape_and_url():
    event = Opportunity(
        event_id="event-1",
        title="Student Cybersecurity Webinar",
        url="https://events.ieee.org/webinar",
        start=datetime(2026, 12, 1, 10, 0, tzinfo=BAHRAIN),
        end=None,
        category="Webinar",
        summary=("A verified technical webinar description with enough detail to help "
                 "university students decide whether to attend and learn cybersecurity topics."),
        online=True,
        student_value=True,
        score=55,
    )
    payload = card_payload([event], datetime(2026, 11, 24, 10, 0, tzinfo=BAHRAIN))
    assert payload["type"] == "message"
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["contentUrl"] is None
    assert attachment["content"]["type"] == "AdaptiveCard"
    assert attachment["content"]["version"] == "1.4"
    assert json.dumps(payload)


def test_card_rejects_oversized_payload(monkeypatch):
    event = Opportunity(
        event_id="event-1",
        title="A" * 1000,
        url="https://events.ieee.org/test",
        start=datetime(2026, 12, 1, tzinfo=BAHRAIN),
        end=None,
        category="Competition",
        summary="S" * 240,
        online=True,
        student_value=True,
    )
    payload = card_payload([event] * 5, datetime(2026, 11, 24, tzinfo=BAHRAIN))
    assert payload["attachments"][0]["content"]["body"]
