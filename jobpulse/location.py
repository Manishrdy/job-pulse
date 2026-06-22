"""Location filtering — keep only jobs in the target country (US by default).

jobhive scrapers return *every* posting a company has, worldwide. We only
want jobs in one country, so each posting is classified against that
country's **state roster** — a ``{code: full_name}`` map of every state /
province / territory. A posting matches the target if its **location field**
(never the description) contains either a state code (``NC``) or the full
state name (``North Carolina``), the country name/synonym, or a matching
``country_iso``.

Rosters are provided for the US (50 states + DC + territories) and India
(states + union territories), so the target switches with one config value
(``location.country_code``). Whichever rostered country isn't the target
becomes a *foreign* signal, alongside a rest-of-world country/city denylist.

Two-letter codes are ambiguous across countries (``TN`` = Tennessee or Tamil
Nadu; ``CA`` = California or Canada; ``DE`` = Delaware or Germany). The
classifier resolves this with tiered precedence so a real US city is kept
and a same-coded foreign city is dropped — see :func:`classify_location`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from jobpulse.config import Location as LocationConfig


class LocationMatch(StrEnum):
    US = "US"          # matches the target country
    NON_US = "NON_US"  # confirmed a different country
    UNKNOWN = "UNKNOWN"


# --- Country state rosters: {code: full name} ------------------------------

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}

IN_STATES = {
    "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam",
    "BR": "Bihar", "CG": "Chhattisgarh", "GA": "Goa", "GJ": "Gujarat",
    "HR": "Haryana", "HP": "Himachal Pradesh", "JH": "Jharkhand",
    "KA": "Karnataka", "KL": "Kerala", "MP": "Madhya Pradesh",
    "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram",
    "NL": "Nagaland", "OD": "Odisha", "PB": "Punjab", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UK": "Uttarakhand", "WB": "West Bengal",
    "AN": "Andaman and Nicobar Islands", "CH": "Chandigarh",
    "DH": "Dadra and Nagar Haveli and Daman and Diu", "DL": "Delhi",
    "JK": "Jammu and Kashmir", "LA": "Ladakh", "LD": "Lakshadweep",
    "PY": "Puducherry",
}

COUNTRY_ROSTERS: dict[str, dict[str, str]] = {"US": US_STATES, "IN": IN_STATES}

COUNTRY_SYNONYMS: dict[str, set[str]] = {
    "US": {"united states", "united states of america", "usa", "u.s.", "us", "stateside"},
    "IN": {"india", "bharat", "republic of india"},
}

# Major cities / metros per country. Used as a *positive* signal when that
# country is the target (so a city-only listing like "Seattle" or "San Jose"
# is recognized), and as a *foreign* signal when it isn't. Country names still
# win, so "San Jose, Costa Rica" is dropped while bare "San Jose" is kept.
COUNTRY_CITIES: dict[str, set[str]] = {
    "US": {
        "new york", "new york city", "nyc", "brooklyn", "manhattan",
        "san francisco", "south san francisco", "san jose", "oakland",
        "seattle", "bellevue", "redmond", "kirkland", "austin", "boston",
        "los angeles", "san diego", "santa monica", "culver city",
        "chicago", "denver", "boulder", "atlanta", "dallas", "fort worth",
        "houston", "miami", "orlando", "tampa", "philadelphia", "phoenix",
        "tempe", "scottsdale", "portland", "san antonio", "washington dc",
        "arlington", "mclean", "reston", "herndon", "pittsburgh", "raleigh",
        "durham", "cary", "charlotte", "nashville", "columbus", "indianapolis",
        "detroit", "ann arbor", "minneapolis", "salt lake city", "lehi",
        "draper", "kansas city", "saint louis", "st. louis", "cincinnati",
        "cleveland", "sacramento", "san mateo", "palo alto", "mountain view",
        "sunnyvale", "santa clara", "cupertino", "menlo park", "irvine",
        "pleasanton", "plano", "bellevue wa", "crystal city",
        # metros / regions
        "bay area", "san francisco bay area", "greater seattle area",
        "silicon valley", "greater boston", "greater los angeles",
        "new york metro", "research triangle",
    },
    "IN": {
        "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "delhi",
        "new delhi", "gurgaon", "gurugram", "noida", "chennai", "kolkata",
        "ahmedabad", "jaipur", "kochi", "coimbatore", "indore", "nagpur",
        "chandigarh", "thiruvananthapuram", "mysore", "mysuru", "vadodara",
    },
}

# --- Rest-of-world (non-rostered) foreign signals --------------------------

ROW_COUNTRIES = {
    "united kingdom", "uk", "great britain", "britain", "england", "scotland",
    "wales", "northern ireland", "ireland", "canada", "germany", "deutschland",
    "france", "spain", "españa", "italy", "italia", "netherlands", "holland",
    "belgium", "luxembourg", "switzerland", "schweiz", "austria", "sweden",
    "norway", "denmark", "finland", "iceland", "poland", "portugal",
    "czech republic", "czechia", "slovakia", "slovenia", "croatia", "hungary",
    "romania", "bulgaria", "greece", "turkey", "türkiye", "russia", "ukraine",
    "belarus", "lithuania", "latvia", "estonia", "brazil", "brasil", "mexico",
    "méxico", "argentina", "chile", "colombia", "peru", "uruguay", "ecuador",
    "venezuela", "costa rica", "panama", "guatemala", "honduras",
    "el salvador", "nicaragua", "dominican republic", "bolivia", "paraguay",
    "japan", "china", "south korea", "north korea", "korea",
    "taiwan", "pakistan", "bangladesh", "sri lanka", "nepal", "singapore",
    "malaysia", "thailand", "vietnam", "viet nam", "philippines", "indonesia",
    "cambodia", "hong kong", "macau", "australia", "new zealand",
    "south africa", "nigeria", "kenya", "ghana", "egypt", "morocco", "tunisia",
    "israel", "united arab emirates", "uae", "saudi arabia", "qatar", "kuwait",
    "bahrain", "oman", "jordan", "lebanon",
}
ROW_REGIONS = {
    "british columbia", "ontario", "quebec", "québec", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland", "labrador",
    "prince edward island", "yukon", "northwest territories", "nunavut",
    "bavaria", "catalonia", "lombardy", "île-de-france",
}
ROW_CITIES = {
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
    "wellington", "kuala lumpur", "jakarta", "bangkok", "manila",
    "ho chi minh", "hanoi", "tokyo", "osaka", "kyoto", "yokohama", "seoul",
    "busan", "beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou",
    "tel aviv", "jerusalem", "haifa", "dubai", "abu dhabi", "doha", "riyadh",
    "cairo", "lagos", "nairobi", "cape town", "johannesburg", "são paulo",
    "sao paulo", "rio de janeiro", "mexico city", "buenos aires", "bogotá",
    "bogota", "santiago", "lima",
}
# Rest-of-world ISO 3166-1 alpha-2 codes (Canada included).
ROW_ISO2 = {
    "GB", "UK", "IE", "FR", "DE", "ES", "PT", "IT", "NL", "BE", "LU", "CH",
    "AT", "SE", "NO", "DK", "FI", "IS", "PL", "CZ", "SK", "HU", "RO", "BG",
    "GR", "HR", "SI", "EE", "LV", "LT", "UA", "RU", "BY", "TR", "BR", "MX",
    "AR", "CL", "CO", "PE", "UY", "EC", "VE", "JP", "CN", "KR", "TW", "PK",
    "BD", "LK", "NP", "SG", "MY", "TH", "VN", "PH", "ID", "KH", "HK", "MO",
    "AU", "NZ", "ZA", "NG", "KE", "GH", "EG", "MA", "TN", "IL", "AE", "SA",
    "QA", "KW", "BH", "OM", "JO", "LB", "CA",
}

_SPLIT_RE = re.compile(r"[,/;|]")


def _phrase_regex(phrases: set[str]) -> re.Pattern:
    body = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(r"\b(?:" + (body or r"(?!x)x") + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class _CountryRules:
    supported: bool
    iso_set: frozenset[str]
    name_re: re.Pattern
    clean_codes: frozenset[str]
    ambig_codes: frozenset[str]
    home_city_re: re.Pattern
    foreign_re: re.Pattern
    foreign_city_re: re.Pattern
    foreign_codes: frozenset[str]


_RULES_CACHE: dict[str, _CountryRules] = {}


def _rules_for(country_code: str) -> _CountryRules:
    code = (country_code or "US").upper()
    cached = _RULES_CACHE.get(code)
    if cached is not None:
        return cached

    if code not in COUNTRY_ROSTERS:
        # Unsupported target: only country_iso can decide.
        rules = _CountryRules(
            supported=False, iso_set=frozenset({code}),
            name_re=_phrase_regex(set()), clean_codes=frozenset(), ambig_codes=frozenset(),
            home_city_re=_phrase_regex(set()),
            foreign_re=_phrase_regex(set()), foreign_city_re=_phrase_regex(set()),
            foreign_codes=frozenset(),
        )
        _RULES_CACHE[code] = rules
        return rules

    roster = COUNTRY_ROSTERS[code]
    home_codes = set(roster)
    home_names = {v.lower() for v in roster.values()} | COUNTRY_SYNONYMS[code]

    foreign_names = set(ROW_COUNTRIES) | set(ROW_REGIONS)
    foreign_cities = set(ROW_CITIES)
    other_codes: set[str] = set()
    for other, other_roster in COUNTRY_ROSTERS.items():
        if other == code:
            continue
        foreign_names |= {v.lower() for v in other_roster.values()} | COUNTRY_SYNONYMS[other]
        foreign_cities |= COUNTRY_CITIES.get(other, set())
        other_codes |= set(other_roster)

    # A code is foreign-decisive only if it doesn't collide with a home code.
    foreign_codes = (ROW_ISO2 | other_codes) - home_codes
    # A home code is ambiguous if it's also a foreign ISO or another country's
    # state code (TN, CA, DE, …) — counted as home only after cities are ruled out.
    ambig = home_codes & (ROW_ISO2 | other_codes)
    clean = home_codes - ambig

    iso_set = frozenset({"US", "USA"}) if code == "US" else frozenset({code})
    rules = _CountryRules(
        supported=True, iso_set=iso_set,
        name_re=_phrase_regex(home_names),
        clean_codes=frozenset(clean), ambig_codes=frozenset(ambig),
        home_city_re=_phrase_regex(COUNTRY_CITIES.get(code, set())),
        foreign_re=_phrase_regex(foreign_names),
        foreign_city_re=_phrase_regex(foreign_cities),
        foreign_codes=frozenset(foreign_codes),
    )
    _RULES_CACHE[code] = rules
    return rules


def classify_location(
    location: str | None,
    country_iso: str | None = None,
    country_code: str = "US",
) -> LocationMatch:
    """Classify a posting's location relative to the target country.

    Returns ``US`` (matches target), ``NON_US`` (a different country), or
    ``UNKNOWN``. Precedence, each tier winning over the ones below:

      1. ``country_iso`` when present (authoritative).
      2. A target country name / synonym, or a target **state full name**.
      3. A foreign country / region name, or a collision-free foreign ISO code.
      4. A *clean* target state code (one not shared with another country).
      5. A target **major city / metro** (Seattle, San Jose, Greater Seattle Area).
      6. A foreign city name.
      7. An *ambiguous* target state code (NC stays US, but TN/CA/DE only
         resolve here, after foreign cities like Chennai/Toronto are excluded).
    """
    rules = _rules_for(country_code)

    if country_iso and country_iso.strip():
        return LocationMatch.US if country_iso.strip().upper() in rules.iso_set else LocationMatch.NON_US

    if not rules.supported or not location or not location.strip():
        return LocationMatch.UNKNOWN

    tokens = {t.strip().upper() for t in _SPLIT_RE.split(location)}

    if rules.name_re.search(location):
        return LocationMatch.US
    if rules.foreign_re.search(location) or (tokens & rules.foreign_codes):
        return LocationMatch.NON_US
    if tokens & rules.clean_codes:
        return LocationMatch.US
    if rules.home_city_re.search(location):
        return LocationMatch.US
    if rules.foreign_city_re.search(location):
        return LocationMatch.NON_US
    if tokens & rules.ambig_codes:
        return LocationMatch.US
    return LocationMatch.UNKNOWN


def is_target_location(
    location: str | None,
    country_iso: str | None,
    is_remote: bool | None,
    config: LocationConfig,
) -> bool:
    """Whether a posting should be ingested for the configured target country.

    Match → keep, confirmed-other-country → drop. UNKNOWN is kept when
    ``keep_unknown`` is set, else only for confirmed-remote roles when
    ``remote_preferred``.
    """
    match = classify_location(location, country_iso, config.country_code)
    if match is LocationMatch.US:
        return True
    if match is LocationMatch.NON_US:
        return False
    if config.keep_unknown:
        return True
    remote = is_remote is True or (bool(location) and "remote" in location.lower())
    return bool(remote and config.remote_preferred)


def purge_non_target_location(conn, config: LocationConfig) -> int:
    """Delete already-stored jobs whose location is a *confirmed different*
    country (never the ambiguous UNKNOWN ones). Returns the count removed."""
    rows = conn.execute("SELECT id, location, country_iso FROM jobs").fetchall()
    doomed = [
        r["id"]
        for r in rows
        if classify_location(r["location"], r["country_iso"], config.country_code)
        is LocationMatch.NON_US
    ]
    for start in range(0, len(doomed), 500):
        chunk = doomed[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", chunk)
    conn.commit()
    return len(doomed)
