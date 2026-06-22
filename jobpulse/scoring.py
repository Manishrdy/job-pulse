"""FTS5-based relevance scoring against target role keywords (FR-01.6).

Relevance is computed at ingest time using SQLite's built-in BM25
ranking over the ``jobs_fts`` index. Each target role becomes an FTS5
phrase; a job's relevance is the BM25 score of its row against the
disjunction of those phrases, with the title weighted heavily so a role
match in the title dominates a stray match in the description.

BM25 in SQLite returns a value that is more negative the better the
match. We negate it so callers see higher = more relevant, and return
0.0 for rows that don't match any role phrase at all.
"""

from __future__ import annotations

import sqlite3

# BM25 column weights for jobs_fts(title, company, description, location).
# Title carries the role signal, so it dominates; description matches
# count for a little; company/location are near-noise for role relevance.
_TITLE_WEIGHT = 10.0
_COMPANY_WEIGHT = 1.0
_DESCRIPTION_WEIGHT = 2.0
_LOCATION_WEIGHT = 1.0


def _escape_phrase(role: str) -> str:
    """Wrap a role term as an FTS5 phrase, escaping embedded quotes.

    FTS5 phrase syntax is a double-quoted string; a literal double quote
    inside is escaped by doubling it. Wrapping in quotes also neutralizes
    any FTS5 operators that might appear in a role name.
    """
    escaped = role.replace('"', '""')
    return f'"{escaped}"'


def build_match_query(target_roles: list[str]) -> str:
    """Build an FTS5 MATCH query that ORs every target role phrase.

    Returns e.g. ``"AI Engineer" OR "Software Engineer"``. Empty/blank
    roles are skipped. Returns an empty string when nothing usable
    remains — callers should treat that as "score everything 0".
    """
    phrases = [_escape_phrase(r.strip()) for r in target_roles if r and r.strip()]
    return " OR ".join(phrases)


def compute_relevance(
    conn: sqlite3.Connection,
    rowid: int,
    match_query: str,
) -> float:
    """Return the BM25 relevance of one ``jobs`` row against the query.

    The row must already be present in ``jobs_fts`` (it is, via the
    INSERT trigger, even within the same uncommitted transaction).
    Returns a positive float for a match, 0.0 when the row matches no
    role phrase or the query is empty.
    """
    if not match_query:
        return 0.0

    row = conn.execute(
        """
        SELECT bm25(jobs_fts, ?, ?, ?, ?) AS score
        FROM jobs_fts
        WHERE jobs_fts MATCH ? AND rowid = ?
        """,
        (
            _TITLE_WEIGHT,
            _COMPANY_WEIGHT,
            _DESCRIPTION_WEIGHT,
            _LOCATION_WEIGHT,
            match_query,
            rowid,
        ),
    ).fetchone()

    if row is None or row["score"] is None:
        return 0.0

    # BM25 is more negative for better matches; negate so higher = better.
    # Clamp tiny positives (theoretically possible) to 0.0.
    score = -float(row["score"])
    return score if score > 0 else 0.0
