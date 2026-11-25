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
_DEGREES = {
    degree: re.compile(p, re.IGNORECASE) for degree, p in DEGREE_PATTERNS.items()
}
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_SEASON_YEAR = re.compile(
    rf"({_SEASON}(?:\s*/\s*{_SEASON})*)\s*(?:,\s*)?(20\d\d)", re.IGNORECASE
)
_YEAR_SEASON = re.compile(
    rf"(20\d\d)\s+({_SEASON}(?:\s*/\s*{_SEASON})*)", re.IGNORECASE
)
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
    return {degree for degree, pattern in _DEGREES.items() if pattern.search(title)}


def terms_named(title: str) -> set[str]:
    found = [(seasons, year) for seasons, year in _SEASON_YEAR.findall(title)]
    found += [(seasons, year) for year, seasons in _YEAR_SEASON.findall(title)]
    return {
        f"{season.strip().lower().replace('autumn', 'fall')} {year}"
        for seasons, year in found
        for season in seasons.split("/")
    }


def is_senior(title: str) -> bool:
    return bool(_SENIOR.search(title))


def mentions_work_authorization(description: str) -> bool:
    return bool(_WORK_AUTHORIZATION.search(description))
