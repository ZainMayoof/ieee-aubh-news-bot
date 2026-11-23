from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser

from main import (
    extract_event_dates,
    is_online,
    normalize_opportunities,
    rejection_reason,
)


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
            "An online competition open to university students. Event date: December 15, 2026.",
            guid="competition-1",
        )
    ], NOW)
    assert accepted == 1
    assert opportunities[0].category == "Competition"
    assert opportunities[0].online is True


def test_missing_venue_does_not_confirm_online():
    assert is_online(entry("IEEE Technical Workshop", "An in-person technical workshop."),
                     "IEEE Technical Workshop An in-person technical workshop.") is False


def test_structured_virtual_field_confirms_online():
    item = entry("IEEE Technical Webinar", "A technical webinar.")
    item["virtual"] = "true"
    assert is_online(item, "IEEE Technical Webinar A technical webinar.") is True


def test_generic_online_word_without_virtual_support_does_not_confirm():
    item = entry("IEEE Data Science Event", "Registration is online. Venue details are unavailable.")
    assert is_online(item, "IEEE Data Science Event Registration is online. "
                            "Venue details are unavailable.") is False


def test_specific_institution_restriction_rejected():
    assert rejection_reason(
        "University Cybersecurity Workshop",
        "Only open to National University of Bahrain students.",
    ) == "other_institution"


def test_structured_event_date_is_used_not_publication_date():
    item = entry("AI Workshop", "Applications close 1 December 2026.")
    item["myieee_rss_aggregator_eventdate"] = "Fri, 11 Dec 2026 10:00:00 +0100"
    item["published"] = "Mon, 23 Nov 2026 19:41:27 +0000"
    start, end = extract_event_dates(item, "AI Workshop Applications close 1 December 2026.", NOW)
    assert start is not None
    assert start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d") == "2026-12-11"


def test_registration_deadline_is_not_used_as_event_date():
    item = entry("AI Competition", "Registration deadline: 15 December 2026.")
    start, end = extract_event_dates(
        item,
        "AI Competition Registration deadline: 15 December 2026.",
        NOW,
    )
    assert start is None


def test_published_timestamp_is_never_used_as_event_date():
    item = entry("AI Workshop", "A technical workshop for university students.")
    item["published"] = "Mon, 23 Nov 2026 19:41:27 +0000"
    start, end = extract_event_dates(
        item,
        "AI Workshop A technical workshop for university students.",
        NOW,
    )
    assert start is None
