import argparse
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import dateparser
import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo


RSS_URL = (
    "https://events.vtools.ieee.org/RST/events/api/public/v8/events/list.rss"
    "?span=now~now%2B30.days&published=true&limit=100"
)
BAHRAIN = ZoneInfo("Asia/Bahrain")
UTC = timezone.utc
STATE_TEMPLATE = {"posted_event_ids": [], "pending_event_ids": []}

REJECT_RULES = (
    ("cancelled", re.compile(r"\b(cancell(?:ed|ation)|canceled)\b", re.I)),
    ("postponed", re.compile(r"\bpostponed\b", re.I)),
    ("private_or_closed", re.compile(
        r"\b(invite[\s-]?only|private (?:event|meeting)|closed registration|"
        r"by invitation|registration (?:is )?(?:closed|not open)|members only)\b",
        re.I,
    )),
    ("internal_administration", re.compile(
        r"\b(ExCom|executive committee|chapter (?:board|administration)|"
        r"officer (?:election|meeting)|board meeting|planning meeting|"
        r"chapter officers|chapter (?:planning|election)|committee (?:meeting|meeting only))\b",
        re.I,
    )),
    ("other_institution", re.compile(
        r"\b(?:only|restricted)\s+(?:open )?(?:to|for)\s+([A-Za-z][A-Za-z0-9 .&'-]{2,50})"
        r"\s+(?:students?|university|college)\b",
        re.I,
    )),
)

POSITIVE_CATEGORY = {
    "Competition": re.compile(
        r"\b(competition|hackathon|design challenge|challenge|contest|coding)\b", re.I
    ),
    "Workshop": re.compile(r"\b(workshop|training|bootcamp|hands-on)\b", re.I),
    "Webinar": re.compile(r"\b(webinar|web conference|online (?:lecture|session)|virtual event)\b", re.I),
    "Conference": re.compile(r"\b(conference|symposium|summit)\b", re.I),
    "Career": re.compile(r"\b(career|internship|job fair|recruitment|professional development)\b", re.I),
}


@dataclass
class Opportunity:
    event_id: str
    title: str
    url: str
    start: datetime
    end: Optional[datetime]
    category: str
    summary: str
    online: bool
    student_value: bool
    score: int = 0


class SourceInterface:
    name: str

    def collect(self) -> list[feedparser.FeedParserDict]:
        raise NotImplementedError


class IEEEVToolsRSSSource(SourceInterface):
    name = "IEEE vTools RSS"

    def collect(self) -> list[feedparser.FeedParserDict]:
        session = requests.Session()
        retry = Retry(total=3, connect=3, read=2, status=3,
                      backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}))
        session.mount("https://", HTTPAdapter(max_retries=retry))
        response = session.get(RSS_URL, timeout=(10, 30))
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not getattr(feed, "entries", None):
            raise ValueError(f"RSS parse failed: {feed.bozo_exception}")
        return list(feed.entries)


def clean_html(value: str) -> str:
    if not value:
        return ""
    value = html.unescape(str(value))
    if "<" not in value:
        text = re.sub(r"\s+", " ", value).strip()
        text = re.sub(r"\b(?:View full details|Read more|Learn more)\b\s*[.:-]*", "", text, flags=re.I)
        return text
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\b(?:View full details|Read more|Learn more)\b\s*[.:-]*", "", text, flags=re.I)
    text = re.sub(r"(?:\b\s*[\r\n]+\s*)+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def summarize(title: str, description: str) -> str:
    text = clean_html(description)
    if not text:
        text = clean_html(title)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = ""
    for sentence in sentences:
        candidate = f"{result} {sentence}".strip()
        if len(candidate) <= 240:
            result = candidate
        else:
            break
    if not result:
        result = text[:240].rstrip(" ,;:")
    if len(result) < 160:
        prefix = clean_html(title)
        combined = f"{prefix}. {result}".strip()
        if len(combined) <= 240:
            result = combined
        elif len(combined) > 240:
            result = combined[:237].rstrip(" ,;.") + "..."
    if len(result) > 240:
        result = result[:237].rstrip(" ,;.") + "..."
    return result


def validate_url(value: str) -> Optional[str]:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return value.strip()


def parse_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        try:
            result = dateutil_parser.isoparse(text)
        except (TypeError, ValueError, OverflowError):
            result = dateparser.parse(
                text,
                settings={"TIMEZONE": "Asia/Bahrain", "RETURN_AS_TIMEZONE_AWARE": True,
                          "PREFER_DATES_FROM": "future"},
            )
    if not result:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=BAHRAIN)
    return result.astimezone(BAHRAIN)


def extract_event_dates(entry, text: str, now: datetime) -> tuple[Optional[datetime], Optional[datetime]]:
    structured_start_keys = (
        "myieee_rss_aggregator_eventdate",
        "ev_start",
        "event_start",
        "start",
        "start_time",
        "startdate",
        "start_date",
        "dtstart",
        "begin",
    )
    for key in structured_start_keys:
        value = entry.get(key)
        if not value:
            continue
        parsed = parse_datetime(value)
        if parsed:
            end = None
            for end_key in ("ev_end", "event_end", "end", "end_time", "enddate",
                            "end_date", "dtend", "finish"):
                if entry.get(end_key):
                    end = parse_datetime(entry.get(end_key))
                    if end:
                        break
            return parsed, end

    month_date_pattern = (
        r"\b(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2},?\s+(?:19|20)\d{2}"
    )
    iso_date_pattern = r"\b(?:19|20)\d{2}-\d{2}-\d{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?\b"
    label_pattern = re.compile(
        r"(?im)(?:^|[.!;\n])[^.!;\n]*(?:event date|event begins|event start|session date|"
        r"starts? on|scheduled for|date and time|live date)[^.!;\n]*",
    )

    candidates = []
    for segment in label_pattern.findall(text):
        lowered = segment.lower()
        if any(marker in lowered for marker in (
            "published", "registration deadline", "application deadline",
            "submission deadline", "deadline", "registration closes", "apply by",
        )):
            continue
        candidates.extend(re.findall(month_date_pattern, segment, re.I))
        candidates.extend(re.findall(iso_date_pattern, segment, re.I))

    for candidate in candidates:
        parsed = parse_datetime(candidate)
        if parsed and parsed.astimezone(UTC) >= now.astimezone(UTC) - __import__("datetime").timedelta(days=1):
            return parsed, None
    return None, None


def extract_title_date_conflict(entry, text: str) -> bool:
    title = clean_html(entry.get("title", ""))
    month_dates = re.findall(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2}(?:,\s*(?:19|20)\d{2})?",
        title,
        re.I,
    )
    iso_dates = re.findall(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", title)
    title_candidates = month_dates + iso_dates
    if not title_candidates:
        return False

    structured_value = entry.get("myieee_rss_aggregator_eventdate")
    if not structured_value:
        for key in ("ev_start", "event_start", "start", "start_time", "startdate",
                    "start_date", "dtstart", "begin"):
            if entry.get(key):
                structured_value = entry.get(key)
                break
    if not structured_value:
        return False

    structured = parse_datetime(structured_value)
    if not structured:
        return False
    structured_date = structured.date()
    for candidate in title_candidates:
        parsed = parse_datetime(candidate)
        if parsed and parsed.date() != structured_date:
            return True
    return False


def extract_verified_deadline(text: str) -> Optional[datetime]:
    deadline_patterns = (
        r"\b(?:application|registration|submission)\s+(?:deadline|closes?|due)\s*:?\s*"
        r"((?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2}(?:,\s*(?:19|20)\d{2})?|"
        r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})",
        r"\b(?:deadline|apply by|applications close)\s*:?\s*"
        r"((?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2}(?:,\s*(?:19|20)\d{2})?|"
        r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})",
    )
    for pattern in deadline_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            parsed = parse_datetime(match.group(1))
            if parsed:
                return parsed
    return None


def classify(text: str) -> str:
    lowered = text.lower()
    funding = re.search(
        r"\b(scholarship application|apply for (?:a )?scholarship|fellowship application|"
        r"funded fellowship|funding award|research grant|grant application|bursary)\b",
        lowered,
        re.I,
    )
    if funding:
        return "Scholarship"
    if re.search(r"\b(hackathon|design challenge|coding competition|competition|contest)\b", lowered):
        return "Competition"
    if re.search(r"\b(webinar|seminar|virtual (?:lecture|workshop)|online (?:workshop|seminar|lecture))\b", lowered):
        return "Webinar"
    if re.search(r"\b(workshop|training|bootcamp|hands-on)\b", lowered):
        return "Workshop"
    if re.search(r"\b(internship|career fair|job fair|career (?:event|development)|recruitment)\b", lowered):
        return "Career"
    if re.search(r"\b(conference|symposium|summit)\b", lowered):
        return "Conference"
    if re.search(r"\b(research (?:opportunity|position|project)|call for (?:research )?papers)\b", lowered):
        return "Conference"
    return "Other"


def is_online(entry, text: str) -> bool:
    virtual = str(entry.get("virtual", "")).strip().lower()
    if virtual in ("true", "yes", "1"):
        return True
    return bool(re.search(
        r"\b(online event|virtual event|virtual (?:workshop|webinar|conference|meeting)|"
        r"online (?:competition|hackathon|challenge|workshop|webinar|conference)|"
        r"join (?:the )?(?:event )?online|participate (?:from )?(?:anywhere|remotely|online)|"
        r"livestream(?:ed)? (?:online|at)|broadcast (?:live )?online)\b",
        text, re.I,
    ))


def is_student_value(text: str) -> bool:
    return bool(re.search(
        r"\b(students?|undergraduates?|graduates?|early-career|university|college)\b",
        text, re.I,
    ))


def is_internationally_open(entry, text: str) -> bool:
    if is_online(entry, text):
        return True
    return bool(re.search(
        r"\b(open (?:to )?(?:all|international|global) (?:students?|participants?|attendees?)|"
        r"students? (?:outside|from other|from abroad)|participants? (?:outside|from abroad)|"
        r"external (?:students?|participants?|attendees?) (?:welcome|eligible)|"
        r"open participation|worldwide (?:participation|registration))\b",
        text,
        re.I,
    ))


def unfamiliar_multilingual_description(title: str, description: str) -> bool:
    combined = f"{title} {description}".strip()
    if not combined:
        return True
    english_terms = re.search(
        r"\b(student|students|university|college|workshop|webinar|seminar|conference|"
        r"competition|scholarship|grant|research|career|engineering|technology|international)\b",
        combined,
        re.I,
    )
    non_ascii = sum(1 for char in combined if ord(char) > 127)
    return non_ascii / max(len(combined), 1) > 0.35 and english_terms is None


def rejection_reason(title: str, description: str, now: Optional[datetime] = None) -> Optional[str]:
    text = f"{title} {description}"
    lowered = text.lower()
    if "expired" in lowered or "registration closed" in lowered:
        return "expired_or_closed"
    if re.search(
        r"\b(already (?:has been |was )?hosted|event (?:has )?(?:already )?taken place|"
        r"this (?:event|webinar|seminar) (?:was|has) (?:previously )?(?:held|hosted)|"
        r"(?:recording|recorded session) (?:is|will be) available)\b",
        text,
        re.I,
    ):
        return "already_hosted"
    if re.search(
        r"\b(ping pong|table tennis|tennis tournament|football tournament|basketball tournament|"
        r"badminton tournament|sports tournament|prize ceremony|award ceremony|"
        r"prize-giving|prize giving|trophy ceremony|ceremonial award|hackathon prize ceremony)\b",
        text,
        re.I,
    ):
        return "sports_or_ceremony"
    if re.search(
        r"\b(social (?:gathering|event)|coffee (?:chat|break)|dinner|lunch|"
        r"networking mixer|birthday|celebration|party|picnic)\b",
        text,
        re.I,
    ):
        return "irrelevant_social_event"
    if re.search(r"\b(?:not|only not)\s+(?:open|available)\s+to\s+(?:students|undergraduates|graduates)\b", text, re.I):
        return "restricted_eligibility"
    if re.search(r"\b(?:faculty|employees?|staff)\s+only\b", text, re.I):
        return "restricted_eligibility"
    for reason, pattern in REJECT_RULES:
        if pattern.search(text):
            return reason
    restricted_eligibility = re.compile(
        r"\b(?:only|restricted)\s+(?:open\s+)?(?:to|for)\s+"
        r"(?:[a-z][a-z0-9 .&'-]{2,60}\s+)?"
        r"(?:students?|members?|employees?|faculty|staff|undergraduates?|graduates?)\b",
        re.I,
    )
    specific_institution = re.compile(
        r"\b(?:only|restricted)\s+(?:to|for)\s+[A-Z][A-Za-z0-9 .&'-]{2,50}"
        r"\s+(?:students?|university|college)\b",
        re.I,
    )
    if restricted_eligibility.search(text) or specific_institution.search(text):
        return "other_institution"
    if now:
        deadline = extract_verified_deadline(text)
        if deadline and deadline.astimezone(UTC) < now.astimezone(UTC):
            return "deadline_passed"
    social = re.compile(
        r"\b(social (?:gathering|event)|coffee (?:chat|break)|dinner|lunch|"
        r"networking mixer|birthday|celebration)\b",
        re.I,
    )
    if social.search(text) and not is_student_value(text):
        return "irrelevant_social_event"
    if re.search(r"\b(chapter meeting|general meeting|agm|annual general meeting)\b", text, re.I):
        if not re.search(r"\b(workshop|webinar|student|career|technical|competition|scholarship)\b", text, re.I):
            return "irrelevant_meeting"
    return None


def rank_score(category: str, online: bool, student_value: bool, start: datetime, now: datetime) -> int:
    category_scores = {
        "Competition": 35, "Scholarship": 35, "Workshop": 25,
        "Webinar": 25, "Conference": 20, "Career": 20, "Other": 5,
    }
    score = category_scores[category]
    if online:
        score += 15
    if student_value:
        score += 15
    days = (start - now).total_seconds() / 86400
    if 0 <= days <= 7:
        score += 20
    elif days <= 30:
        score += 12
    elif days <= 90:
        score += 5
    return score


def stable_id(entry) -> tuple[Optional[str], Optional[str]]:
    guid = entry.get("guid") or entry.get("id")
    links = entry.get("links") or []
    link_href = links[0].get("href", "") if links else ""
    url = validate_url(entry.get("link") or link_href)
    if guid:
        return str(guid).strip(), url
    if url:
        return url, url
    return None, None


def normalize_opportunities(entries: Iterable, now: datetime) -> tuple[list[Opportunity], dict[str, int], int]:
    normalized = []
    rejected = {}
    accepted = 0
    for entry in entries:
        title = clean_html(entry.get("title", ""))
        description = clean_html(entry.get("description") or entry.get("summary") or "")
        if not title:
            rejected["missing_title"] = rejected.get("missing_title", 0) + 1
            continue
        reason = rejection_reason(title, description, now)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        if unfamiliar_multilingual_description(title, description):
            rejected["multilingual_unclear"] = rejected.get("multilingual_unclear", 0) + 1
            continue
        event_id, url = stable_id(entry)
        if not event_id or not url:
            rejected["missing_stable_id_or_url"] = rejected.get("missing_stable_id_or_url", 0) + 1
            continue
        start, end = extract_event_dates(entry, f"{title} {description}", now)
        if not start:
            rejected["uncertain_date"] = rejected.get("uncertain_date", 0) + 1
            continue
        if extract_title_date_conflict(entry, f"{title} {description}"):
            rejected["title_structured_date_conflict"] = rejected.get("title_structured_date_conflict", 0) + 1
            continue
        if start.astimezone(UTC) < now.astimezone(UTC):
            rejected["expired"] = rejected.get("expired", 0) + 1
            continue
        if end and end.astimezone(UTC) < now.astimezone(UTC):
            rejected["expired"] = rejected.get("expired", 0) + 1
            continue
        text = f"{title} {description}"
        category = classify(text)
        if category == "Other":
            if re.search(
                r"\b(student|students|undergraduate|university|college|engineering|ai|"
                r"software|cybersecurity|electronics|robotics|data science|technical)\b",
                text, re.I,
            ):
                category = "Other"
            else:
                rejected["not_relevant"] = rejected.get("not_relevant", 0) + 1
                continue
        online = is_online(entry, text)
        if not is_internationally_open(entry, text):
            rejected["international_accessibility_unverified"] = rejected.get(
                "international_accessibility_unverified", 0
            ) + 1
            continue
        student_value = is_student_value(text)
        opportunity = Opportunity(
            event_id=event_id,
            title=title,
            url=url,
            start=start,
            end=end,
            category=category,
            summary=summarize(title, description),
            online=online,
            student_value=student_value,
        )
        opportunity.score = rank_score(category, online, student_value, start, now)
        normalized.append(opportunity)
        accepted += 1
    return normalized, rejected, accepted


def deduplicate(opportunities: Iterable[Opportunity], posted_ids: set[str]) -> list[Opportunity]:
    unique = {}
    for event in opportunities:
        if event.event_id in posted_ids:
            continue
        key = event.url.rstrip("/").lower()
        existing = unique.get(key)
        if existing is None or event.score > existing.score:
            unique[key] = event
    return sorted(unique.values(), key=lambda event: (
        -event.score, event.start, event.title.lower(), event.event_id
    ))


def read_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("posted_event_ids"), list):
            raise ValueError("state must contain posted_event_ids")
        return {str(value) for value in data["posted_event_ids"]}
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read safe published-state file {path}: {exc}") from exc


def read_pending_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pending = data.get("pending_event_ids", []) if isinstance(data, dict) else []
        if not isinstance(pending, list):
            raise ValueError("state pending_event_ids must be a list")
        return {str(value) for value in pending}
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read safe published-state file {path}: {exc}") from exc


def write_state(path: Path, posted_ids: set[str], newly_published: Iterable[str],
                pending_ids: Optional[Iterable[str]] = None) -> None:
    ids = set(posted_ids)
    ids.update(str(value) for value in newly_published)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = set(pending_ids or [])
    payload = {
        "posted_event_ids": sorted(ids),
        "pending_event_ids": sorted(pending),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def reserve_state(path: Path, posted_ids: set[str], pending_ids: Iterable[str]) -> None:
    write_state(path, posted_ids, [], pending_ids)


def finalize_state(path: Path, posted_ids: set[str], published_ids: Iterable[str],
                   pending_ids: Iterable[str]) -> None:
    write_state(path, posted_ids, published_ids, [])


def format_bahrain(dt: datetime) -> str:
    return dt.astimezone(BAHRAIN).strftime("%d %b %Y, %I:%M %p") + " (+03)"


def sanitize_card_text(value: str) -> str:
    value = clean_html(value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value.strip()


def card_payload(events: list[Opportunity], now: datetime) -> dict:
    if not 1 <= len(events) <= 5:
        raise RuntimeError("Adaptive Card requires 1-5 events")
    body = [
        {
            "type": "TextBlock",
            "text": sanitize_card_text("IEEE AUBH | Student Opportunities"),
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": f"Generated {format_bahrain(now)}",
            "isSubtle": True,
            "wrap": True,
            "spacing": "None",
        },
    ]
    for event in events:
        url = validate_url(event.url)
        if not url:
            raise RuntimeError("Adaptive Card contains an invalid event URL")
        body.append({"type": "ColumnSet", "spacing": "Medium", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "text": sanitize_card_text(event.category),
                 "weight": "Bolder", "size": "Small", "color": "Accent", "wrap": True},
                {"type": "TextBlock", "text": sanitize_card_text(event.title),
                 "weight": "Bolder", "size": "Medium", "wrap": True},
                {"type": "TextBlock",
                 "text": f"Verified date: {format_bahrain(event.start)}",
                 "wrap": True, "spacing": "Small"},
                {"type": "TextBlock", "text": sanitize_card_text(event.summary),
                 "wrap": True, "spacing": "Small"},
                {"type": "TextBlock",
                 "text": "Online/virtual: explicitly confirmed" if event.online else "Accessibility: not verified",
                 "isSubtle": True, "wrap": True, "spacing": "Small"},
                {"type": "ActionSet", "actions": [{
                    "type": "Action.OpenUrl", "title": "View official opportunity",
                    "url": url,
                }]},
            ]}
        ]})
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    payload = {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "contentUrl": None,
        "content": card,
    }]}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) >= 256 * 1024:
        raise RuntimeError("Adaptive Card payload exceeds 256 KB")
    return payload


def post_card(webhook_url: str, payload: dict) -> bool:
    if not validate_url(webhook_url):
        logging.error("TEAMS_WEBHOOK_URL is missing or invalid")
        return False
    response = None
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=(10, 30),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return True
    except requests.Timeout:
        logging.error("Teams webhook timed out; no retry was attempted")
    except requests.RequestException as exc:
        status = getattr(response, "status_code", "unknown")
        logging.error("Teams webhook POST failed with status %s", status)
        logging.debug("Teams POST exception: %s", exc.__class__.__name__)
    return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Publish the IEEE AUBH Student Opportunities digest")
    parser.add_argument("--dry-run", action="store_true", help="Never post or update state")
    parser.add_argument("--send", action="store_true", help="Attempt sending; environment guard is still required")
    parser.add_argument("--state-file", default="state/posted_events.json")
    parser.add_argument("--reserve-state", action="store_true",
                        help="Reserve selected event IDs for a later live send")
    parser.add_argument("--resume-reservation", action="store_true",
                        help="Send only the IDs reserved in the state file")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    env_dry = os.getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes", "on")
    env_send = os.getenv("SEND_TO_TEAMS", "false").strip().lower() in ("1", "true", "yes", "on")
    dry_run = args.dry_run or not args.send or env_dry

    now = datetime.now(BAHRAIN)
    try:
        entries = IEEEVToolsRSSSource().collect()
    except requests.RequestException as exc:
        logging.error("IEEE vTools source failed: HTTP/network error (%s)", exc.__class__.__name__)
        return 0
    except (ValueError, feedparser.CharacterEncodingOverride) as exc:
        logging.error("IEEE vTools source failed safely: %s", exc)
        return 0

    opportunities, rejected, accepted = normalize_opportunities(entries, now)
    logging.info("source_count=%d accepted_count=%d", len(entries), accepted)
    for reason in sorted(rejected):
        logging.info("rejected_reason=%s count=%d", reason, rejected[reason])

    state_path = Path(args.state_file)
    try:
        posted_ids = read_state(state_path)
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1

    if args.reserve_state:
        pending_ids = read_pending_ids(state_path)
        if pending_ids:
            logging.info("selected_count=0; pending reservation already exists; refusing to reserve or post duplicates")
            return 1
        selected = deduplicate(opportunities, posted_ids)[:5]
        if len(selected) < 1:
            logging.info("selected_count=%d; no qualifying unique events, so no state was reserved", len(selected))
            return 1
        selected = selected[:5]
        reserve_state(state_path, posted_ids, [event.event_id for event in selected])
        logging.info("selected_count=%d reserved_ids=%s",
                     len(selected), ",".join(event.event_id for event in selected))
        return 0

    pending_ids = read_pending_ids(state_path)
    if args.resume_reservation:
        if not pending_ids:
            logging.info("selected_count=0; no durable reservation exists")
            return 0
        selected = [event for event in deduplicate(opportunities, posted_ids)
                    if event.event_id in pending_ids][:5]
    else:
        selected = deduplicate(opportunities, posted_ids | pending_ids)[:5]

    if len(selected) < 1:
        logging.info("selected_count=%d; no qualifying unique events, so nothing was posted", len(selected))
        return 0
    selected = selected[:5]
    published_ids = [event.event_id for event in selected]
    logging.info("selected_count=%d published_ids=%s", len(selected),
                 ",".join(published_ids))

    if dry_run:
        logging.info("DRY_RUN: no Teams POST and no persistent state update")
        return 0
    if not args.send or not env_send:
        logging.info("Sending disabled: use --send and SEND_TO_TEAMS=true with DRY_RUN=false")
        return 0
    if env_dry:
        logging.info("Sending disabled: DRY_RUN is true")
        return 0

    webhook = os.getenv("TEAMS_WEBHOOK_URL", "")
    payload = card_payload(selected, now)
    if post_card(webhook, payload):
        write_state(state_path, posted_ids, published_ids, [])
        logging.info("Teams webhook POST returned success; state update written")
        return 0
    logging.error("Teams POST did not succeed; persistent state was not changed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
