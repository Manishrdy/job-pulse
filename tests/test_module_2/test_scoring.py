from __future__ import annotations

import sqlite3

from jobpulse.scoring import build_match_query, compute_relevance


def test_build_match_query_basic():
    q = build_match_query(["AI Engineer", "Software Engineer"])
    assert q == '"AI Engineer" OR "Software Engineer"'


def test_build_match_query_empty():
    assert build_match_query([]) == ""


def test_build_match_query_skips_blanks():
    q = build_match_query(["", "  ", "Backend Engineer"])
    assert q == '"Backend Engineer"'


def test_build_match_query_escapes_quotes():
    q = build_match_query(['Eng "X"'])
    assert q == '"Eng ""X"""'


def _insert_job(conn: sqlite3.Connection, title: str, **cols) -> int:
    fields = {
        "global_id": f"gh:{title}",
        "url": "https://example.com",
        "title": title,
        "company": "Acme",
        "ats_type": "greenhouse",
        "description": cols.get("description", ""),
        "location": cols.get("location", ""),
    }
    cur = conn.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, description, location)
           VALUES (:global_id, :url, :title, :company, :ats_type, :description, :location)""",
        fields,
    )
    conn.commit()
    return cur.lastrowid


def test_relevance_positive_for_matching_title(test_db: sqlite3.Connection):
    rowid = _insert_job(test_db, "Software Engineer")
    q = build_match_query(["Software Engineer"])
    score = compute_relevance(test_db, rowid, q)
    assert score > 0


def test_relevance_zero_for_non_matching_title(test_db: sqlite3.Connection):
    rowid = _insert_job(test_db, "Marketing Manager")
    q = build_match_query(["Software Engineer"])
    score = compute_relevance(test_db, rowid, q)
    assert score == 0.0


def test_relevance_zero_for_empty_query(test_db: sqlite3.Connection):
    rowid = _insert_job(test_db, "Software Engineer")
    assert compute_relevance(test_db, rowid, "") == 0.0


def test_title_match_outranks_description_match(test_db: sqlite3.Connection):
    title_hit = _insert_job(test_db, "Backend Engineer")
    desc_hit = _insert_job(
        test_db, "Office Manager", description="We need a Backend Engineer mindset"
    )
    q = build_match_query(["Backend Engineer"])
    title_score = compute_relevance(test_db, title_hit, q)
    desc_score = compute_relevance(test_db, desc_hit, q)
    assert title_score > desc_score > 0
