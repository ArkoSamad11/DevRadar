import re
from enum import StrEnum


class Degree(StrEnum):
    bachelors = "bachelors"
    masters = "masters"
    mba = "mba"
    phd = "phd"


DEGREE_PATTERNS = {
    Degree.bachelors: r"\bb\.?s\.?c?\b|\bbachelor|\bundergrad",
    Degree.masters: r"\bm\.?s\.?c?\b(?!\s*(?:office|excel|teams|word|sql|dynamics|access))|\bmaster'?s\b",
    Degree.mba: r"\bmba\b",
    Degree.phd: r"\bph\.?\s?d\b|\bdoctora",
}
DEGREE_REGEXES = {
    degree: re.compile(p, re.IGNORECASE) for degree, p in DEGREE_PATTERNS.items()
}
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_SEASON_YEAR = re.compile(
    rf"({_SEASON}(?:\s*/\s*{_SEASON})*)\s*(?:,\s*)?(20\d\d)", re.IGNORECASE
)
_YEAR_SEASON = re.compile(
    rf"(20\d\d)\s+({_SEASON}(?:\s*/\s*{_SEASON})*)", re.IGNORECASE
)
_SEASON_WORD = re.compile(rf"\b{_SEASON}\b", re.IGNORECASE)
_YEAR = re.compile(r"\b20(\d\d)\b|'(\d\d)\b")
_SENIOR = re.compile(
    r"\b(?:senior|sr|staff|principal|lead|manager|director|head|vp|vice president|chief|architect"
    r"|distinguished|fellow|expert|partner|supervisor|ii|iii|iv|v)\b",
    re.IGNORECASE,
)
_WORK_AUTHORIZATION = re.compile(
    r"u\.?s\.? citizen|united states citizen|security clearance|u\.?s\.? person\b|\bitar\b",
    re.IGNORECASE,
)


def degrees_named(title: str) -> set[Degree]:
    return {
        degree for degree, pattern in DEGREE_REGEXES.items() if pattern.search(title)
    }


def terms_named(title: str) -> set[str]:
    """Terms such as "summer 2027"; a season alone, as in "(SPRING) Analyst Intern", is "spring"."""
    found = [(seasons, year) for seasons, year in _SEASON_YEAR.findall(title)]
    found += [(seasons, year) for year, seasons in _YEAR_SEASON.findall(title)]
    if not found:
        seasons = "/".join(_SEASON_WORD.findall(title))
        years = {f"20{a or b}" for a, b in _YEAR.findall(title)}
        if seasons:
            found = [(seasons, year) for year in years or {""}]
    return {
        f"{season.strip().lower().replace('autumn', 'fall')} {year}".strip()
        for seasons, year in found
        for season in seasons.split("/")
    }


def admits_term(named: set[str] | frozenset, term: str) -> bool:
    return not named or term in named or term.split()[0] in named


def is_senior(title: str) -> bool:
    return bool(_SENIOR.search(title))


def mentions_work_authorization(description: str) -> bool:
    return bool(_WORK_AUTHORIZATION.search(description))
