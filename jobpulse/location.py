"""Location filtering — keep only target-country (default US) jobs.

jobhive scrapers return *every* posting a company has, worldwide. We only
want US roles (plus remote), so this module classifies a posting's location
and the ingestion path drops anything that isn't a match.

Signals, in order of authority:

1. ``country_iso`` — when a scraper populates the structured ISO country
   code, it's decisive (``US`` → keep, anything else → drop). Many ATSes
   leave it ``None`` though, so we fall back to:
2. Free-text ``location`` — matched against US state names / postal
   abbreviations / synonyms (allow) and a denylist of foreign countries,
   regions, cities and ISO codes (deny). Matching is word-boundary based
   and collision-safe: 2-letter foreign ISO codes that clash with a US
   state abbreviation (DE, IN, CO, ID, IL, …) are never used for denial.

A US signal always wins over a foreign one (a "US or Canada" role is
US-eligible). When neither fires the location is ``UNKNOWN`` (bare
"Remote", empty, "Multiple locations") and the keep/drop decision is left
to policy (see :func:`is_target_location`).

The text rules are US-specialized. For a different ``country_code`` (e.g.
a future India switch) classification falls back to ``country_iso`` only.
"""

from __future__ import annotations

import re
from enum import StrEnum

from jobpulse.config import Location as LocationConfig


class LocationMatch(StrEnum):
    US = "US"
    NON_US = "NON_US"
    UNKNOWN = "UNKNOWN"


# --- US data ---------------------------------------------------------------

US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "GU", "VI", "AS", "MP",
}

# Full state names. "Georgia" is intentionally omitted — it collides with the
# country Georgia; US Georgia postings use the "GA" abbreviation in practice.
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "puerto rico",
}
_US_SYNONYMS = {"united states", "united states of america", "usa", "us", "stateside"}

# --- Foreign data ----------------------------------------------------------

_FOREIGN_COUNTRIES = {
    "united kingdom", "uk", "great britain", "britain", "england", "scotland",
    "wales", "northern ireland", "ireland", "canada", "germany", "deutschland",
    "france", "spain", "españa", "italy", "italia", "netherlands", "holland",
    "belgium", "luxembourg", "switzerland", "schweiz", "austria", "sweden",
    "norway", "denmark", "finland", "iceland", "poland", "portugal",
    "czech republic", "czechia", "slovakia", "slovenia", "croatia", "hungary",
    "romania", "bulgaria", "greece", "turkey", "türkiye", "russia", "ukraine",
    "belarus", "lithuania", "latvia", "estonia", "brazil", "brasil", "mexico",
    "méxico", "argentina", "chile", "colombia", "peru", "uruguay", "ecuador",
    "venezuela", "japan", "china", "south korea", "north korea", "korea",
    "taiwan", "india", "pakistan", "bangladesh", "sri lanka", "nepal",
    "singapore", "malaysia", "thailand", "vietnam", "viet nam", "philippines",
    "indonesia", "cambodia", "hong kong", "macau", "australia", "new zealand",
    "south africa", "nigeria", "kenya", "ghana", "egypt", "morocco", "tunisia",
    "israel", "united arab emirates", "uae", "saudi arabia", "qatar", "kuwait",
    "bahrain", "oman", "jordan", "lebanon",
}
# US state abbreviations that are ALSO notable foreign country ISO codes, so a
# bare token is ambiguous (CA=California/Canada, DE=Delaware/Germany, …). These
# only count as a US signal after foreign city names have been ruled out.
AMBIG_US_ABBREVS = {"CA", "CO", "DE", "ID", "IL", "IN", "AR", "MA", "PA"}
CLEAN_US_ABBREVS = US_STATE_ABBREVS - AMBIG_US_ABBREVS

_FOREIGN_REGIONS = {
    "british columbia", "ontario", "quebec", "québec", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland", "labrador",
    "prince edward island", "yukon", "northwest territories", "nunavut",
    "bavaria", "catalonia", "lombardy", "île-de-france",
}
_FOREIGN_CITIES = {
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh",
    "bristol", "cardiff", "belfast", "dublin", "cork", "berlin", "munich",
    "münchen", "hamburg", "frankfurt", "cologne", "köln", "düsseldorf",
    "stuttgart", "paris", "lyon", "marseille", "toulouse", "madrid",
    "barcelona", "valencia", "lisbon", "porto", "amsterdam", "rotterdam",
    "the hague", "utrecht", "brussels", "antwerp", "zurich", "zürich",
    "geneva", "bern", "basel", "vienna", "wien", "stockholm", "gothenburg",
    "oslo", "copenhagen", "helsinki", "warsaw", "kraków", "krakow", "prague",
    "praha", "budapest", "bucharest", "athens", "istanbul", "moscow", "kyiv",
    "kiev", "toronto", "vancouver", "burnaby", "montreal", "montréal",
    "ottawa", "calgary", "edmonton", "winnipeg", "waterloo", "mississauga",
    "sydney", "melbourne", "brisbane", "perth", "adelaide", "auckland",
    "wellington", "bangalore", "bengaluru", "hyderabad", "mumbai", "pune",
    "delhi", "new delhi", "gurgaon", "gurugram", "noida", "chennai",
    "kolkata", "ahmedabad", "kuala lumpur", "jakarta", "bangkok", "manila",
    "ho chi minh", "hanoi", "tokyo", "osaka", "kyoto", "yokohama", "seoul",
    "busan", "beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou",
    "tel aviv", "jerusalem", "haifa", "dubai", "abu dhabi", "doha", "riyadh",
    "cairo", "lagos", "nairobi", "cape town", "johannesburg", "são paulo",
    "sao paulo", "rio de janeiro", "mexico city", "buenos aires", "bogotá",
    "bogota", "santiago", "lima",
}

# Foreign ISO-3166 alpha-2 codes, minus any that collide with a US state
# abbreviation (so "DE"=Delaware / "IN"=Indiana etc. are never read as foreign).
_COMMON_FOREIGN_ISO2 = {
    "GB", "UK", "IE", "FR", "DE", "ES", "PT", "IT", "NL", "BE", "LU", "CH",
    "AT", "SE", "NO", "DK", "FI", "IS", "PL", "CZ", "SK", "HU", "RO", "BG",
    "GR", "HR", "SI", "EE", "LV", "LT", "UA", "RU", "BY", "TR", "BR", "MX",
    "AR", "CL", "CO", "PE", "UY", "EC", "VE", "JP", "CN", "KR", "TW", "IN",
    "PK", "BD", "LK", "NP", "SG", "MY", "TH", "VN", "PH", "ID", "KH", "HK",
    "MO", "AU", "NZ", "ZA", "NG", "KE", "GH", "EG", "MA", "TN", "IL", "AE",
    "SA", "QA", "KW", "BH", "OM", "JO", "LB",
}
FOREIGN_CODES = _COMMON_FOREIGN_ISO2 - US_STATE_ABBREVS


def _phrase_regex(phrases: set[str]) -> re.Pattern:
    ordered = sorted(phrases, key=len, reverse=True)
    body = "|".join(re.escape(p) for p in ordered)
    return re.compile(r"\b(?:" + body + r")\b", re.IGNORECASE)


_US_RE = _phrase_regex(_US_STATE_NAMES | _US_SYNONYMS)
# Country/region names are decisive foreign signals; city names are weaker
# (a US city can share a name) so they're matched only after US abbreviations.
_FOREIGN_CR_RE = _phrase_regex(_FOREIGN_COUNTRIES | _FOREIGN_REGIONS)
_FOREIGN_CITY_RE = _phrase_regex(_FOREIGN_CITIES)

_SPLIT_RE = re.compile(r"[,/;|]")


def classify_location(
    location: str | None,
    country_iso: str | None = None,
    country_code: str = "US",
) -> LocationMatch:
    """Classify a posting as US / NON_US / UNKNOWN (text rules are US-only).

    Precedence (each tier wins over the ones below):
      1. ``country_iso`` when present (authoritative).
      2. Explicit US text — synonyms or full state names.
      3. Foreign country / region name, or a collision-free foreign ISO code.
      4. A *clean* US state abbreviation (one that isn't also a country code).
      5. A foreign city name.
      6. An *ambiguous* US abbreviation (CA/DE/IN/…) once cities are ruled out.
    """
    if country_iso and country_iso.strip():
        cc = country_iso.strip().upper()
        return LocationMatch.US if cc in {"US", "USA"} else LocationMatch.NON_US

    # Non-US target country: we only have ISO-based certainty above.
    if country_code.upper() not in {"US", "USA"}:
        return LocationMatch.UNKNOWN

    if not location or not location.strip():
        return LocationMatch.UNKNOWN

    tokens = {t.strip().upper() for t in _SPLIT_RE.split(location)}

    if _US_RE.search(location):
        return LocationMatch.US
    if _FOREIGN_CR_RE.search(location) or (tokens & FOREIGN_CODES):
        return LocationMatch.NON_US
    if tokens & CLEAN_US_ABBREVS:
        return LocationMatch.US
    if _FOREIGN_CITY_RE.search(location):
        return LocationMatch.NON_US
    if tokens & AMBIG_US_ABBREVS:
        return LocationMatch.US
    return LocationMatch.UNKNOWN


def is_target_location(
    location: str | None,
    country_iso: str | None,
    is_remote: bool | None,
    config: LocationConfig,
) -> bool:
    """Decide whether a posting should be ingested for the target country.

    US → keep, NON_US → drop. UNKNOWN is kept when ``keep_unknown`` is set;
    otherwise kept only for confirmed-remote roles when ``remote_preferred``.
    """
    match = classify_location(location, country_iso, config.country_code)
    if match is LocationMatch.US:
        return True
    if match is LocationMatch.NON_US:
        return False
    # UNKNOWN
    if config.keep_unknown:
        return True
    remote = is_remote is True or (bool(location) and "remote" in location.lower())
    return bool(remote and config.remote_preferred)


def purge_non_target_location(conn, config: LocationConfig) -> int:
    """Delete already-stored jobs whose location is *decisively* foreign.

    Only removes ``NON_US`` rows (never the ambiguous UNKNOWN ones), so a
    cleaner location ruleset clears out leaked foreign jobs without risking
    US postings that simply have a sparse location string. Returns the count.
    """
    rows = conn.execute("SELECT id, location, country_iso FROM jobs").fetchall()
    doomed = [
        r["id"]
        for r in rows
        if classify_location(r["location"], r["country_iso"], config.country_code)
        is LocationMatch.NON_US
    ]
    for chunk_start in range(0, len(doomed), 500):
        chunk = doomed[chunk_start : chunk_start + 500]
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", chunk)
    conn.commit()
    return len(doomed)
