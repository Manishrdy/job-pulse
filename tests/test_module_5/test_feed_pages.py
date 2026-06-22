from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import seed_job


def test_feed_returns_200(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "JobPulse" in resp.text


def test_feed_contains_job_cards(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Senior Backend Engineer", company="Acme Corp")
    resp = client.get("/")
    assert "Senior Backend Engineer" in resp.text
    assert "Acme Corp" in resp.text
    assert 'class="job-card"' in resp.text


def test_card_displays_required_fields(client: TestClient, test_db: sqlite3.Connection):
    seed_job(
        test_db,
        title="AI Engineer",
        company="DeepCorp",
        ats_type="greenhouse",
        location="Remote, US",
        is_remote=1,
        salary_min=150000.0,
        salary_max=200000.0,
        salary_currency="USD",
        salary_period="YEAR",
        employment_type="FULL_TIME",
        relevance_score=4.2,
        posted_at="2026-06-20T00:00:00Z",
    )
    html = client.get("/").text
    assert "AI Engineer" in html          # title
    assert "DeepCorp" in html             # company
    assert "greenhouse" in html           # ats type
    assert "Remote, US" in html           # location
    assert "badge-remote" in html         # remote badge
    assert "150,000" in html              # salary
    assert "Full Time" in html            # employment type
    assert "4.2" in html                  # relevance
    assert "Posted 2026-06-20" in html    # posted date


def test_new_badge_shown_when_unviewed(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Fresh Role", viewed_at=None)
    html = client.get("/").text
    assert "badge-new" in html


def test_action_buttons_wired_to_endpoints(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Wired Role", apply_url="https://apply.example.com/x")
    html = client.get("/").text
    assert f'hx-post="/job/{jid}/apply"' in html
    assert f'hx-post="/job/{jid}/expire"' in html
    assert f'hx-post="/job/{jid}/block"' in html
    assert "https://apply.example.com/x" in html  # external Apply link


def test_empty_state(client: TestClient):
    html = client.get("/").text
    assert "No jobs match" in html


def test_filter_params_reflected(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Backend Engineer", ats_type="lever")
    seed_job(test_db, title="Marketing Manager", ats_type="greenhouse")
    html = client.get("/", params={"role": "Backend Engineer"}).text
    assert "Backend Engineer" in html
    assert "Marketing Manager" not in html
    # filter value repopulated into the form
    assert 'value="Backend Engineer"' in html


def test_filter_ats_dropdown_selected(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, ats_type="lever")
    html = client.get("/", params={"ats": "lever"}).text
    assert 'value="lever" selected' in html


def test_partial_route_returns_cards_only(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Partial Role")
    resp = client.get("/partials/jobs")
    assert resp.status_code == 200
    assert "Partial Role" in resp.text
    # partial must not include the full page chrome
    assert "<html" not in resp.text
    assert "topbar" not in resp.text


def test_blocked_and_expired_excluded_from_feed(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Visible Role")
    seed_job(test_db, title="Blocked Role", is_blocked=1)
    seed_job(test_db, title="Expired Role", status="expired")
    html = client.get("/").text
    assert "Visible Role" in html
    assert "Blocked Role" not in html
    assert "Expired Role" not in html


def test_pagination_controls(client: TestClient, test_db: sqlite3.Connection):
    for i in range(25):
        seed_job(test_db, title=f"Role {i}", first_seen="2026-06-20T00:00:00Z")
    html = client.get("/").text
    assert "Next →" in html
    assert "Page 1 of 2" in html


def test_nav_links_present(client: TestClient):
    html = client.get("/").text
    for href in ['href="/"', 'href="/applied"', 'href="/analytics"', 'href="/blocklist"', 'href="/scrape-logs"']:
        assert href in html
