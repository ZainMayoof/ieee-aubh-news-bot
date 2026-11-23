from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser

from main import normalize_opportunities, rejection_reason


BAHRAIN = ZoneInfo("Asia/Bahrain")
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=BAHRAIN)


def entry(title, description, guid="test-guid", link="https://events.vtools.ieee.org/test"):
    return feedparser.FeedParserDict({
        "title": title,
        "description": description,
        "link": link,
        "guid": guid,
    })


def test_cancelled_rejected():
    assert rejection_reason("IEEE Student Webinar", "This event was CANCELLED.") == "cancelled"


def test_internal_committee_with_students_rejected():
    text = "ExCom planning meeting for student chapter officers"
    assert rejection_reason(text, "Students will discuss board planning.") == "internal_administration"


def test_invite_only_rejected():
    assert rejection_reason("Private technical webinar", "Invite-only meeting; closed registration.") == "private_or_closed"


def test_rejects_missing_date():
    opportunities, rejected, accepted = normalize_opportunities([
        entry("AI Workshop", "A technical workshop for university students.")
    ], NOW)
    assert accepted == 0
    assert opportunities == []
    assert rejected["uncertain_date"] == 1


def test_accepts_verified_global_student_competition():
    opportunities, rejected, accepted = normalize_opportunities([
        entry(
            "IEEE Global Student AI Hackathon",
            "An online competition open to university students. December 15, 2026.",
            guid="competition-1",
        )
    ], NOW)
    assert accepted == 1
    assert opportunities[0].category == "Competition"
    assert opportunities[0].online is True
