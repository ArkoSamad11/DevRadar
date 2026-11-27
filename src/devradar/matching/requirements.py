import re
from dataclasses import dataclass
from datetime import date

from devradar.matching.eligibility import DEGREE_REGEXES, Degree, degrees_named

LEVELS = (Degree.bachelors, Degree.masters, Degree.phd)

_ABBREVIATIONS = [
    (re.compile(r"\bph\.?\s?d\.?(?=\W|$)", re.IGNORECASE), "PhD"),
    (
        re.compile(
            r"\bb\.\s?s\.?(?:c\.?)?(?=\W|$)|\bb\.\s?a\.?(?=\W|$)", re.IGNORECASE
        ),
        "BS",
    ),
    (re.compile(r"\bm\.\s?s\.?(?:c\.?)?(?=\W|$)", re.IGNORECASE), "MS"),
    (re.compile(r"\bu\.\s?s\.?(?:a\.?)?(?=\W|$)", re.IGNORECASE), "US"),
]
# A spaced hyphen starts a list item, except inside a range such as "Dec 2027 - 2029".
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|(?<!\d)\s-\s(?=[A-Z])|\s[•●▪·*]\s|\n")
# Wording that says who the job is for, as opposed to describing colleagues or the company.
_APPLICANT = re.compile(
    r"\b(?:pursuing|enrolled|enrollment|working towards?"
    r"|studying|candidates?\b.{0,40}\b(?:for|in) an?|degree in progress|must be an?"
    r"|completing|completed|obtaining|seeking an?|looking for)\b"
    r"|\b(?:PhD|doctoral|master'?s|masters|bachelor'?s|graduate|undergraduate)[ -](?:level |research )*"
    r"(?:students?|interns?|candidates?|co-?op)\b"
    r"|\bqualifications\b.{0,25}\b(?:BS|MS|PhD|bachelor|master)|\bdegrees? (?:in|or|from)\b"
    r"|\b(?:junior|senior|sophomore)s?\b.{0,30}\bstudents?\b"
    r"|\bpostdoc",
    re.IGNORECASE,
)
_OPTIONAL = re.compile(
    r"\b(?:prefer(?:red|ably|ence)?|nice to have|bonus|a plus|ideally|desired)\b",
    re.IGNORECASE,
)
_OR_HIGHER = re.compile(
    r"or (?:higher|above|beyond|advanced degree|more advanced)|and (?:above|beyond)",
    re.IGNORECASE,
)
_ADVANCED = re.compile(
    r"\b(?:advanced|graduate)(?:[ -]level)? degrees?\b", re.IGNORECASE
)
_GRADUATE = re.compile(
    r"(?<!under)(?<!recent )(?<!new )\bgraduate\b,?(?= (?:students?|degree|program|or PhD|level)|-level"
    r"| or | and |,)",
    re.IGNORECASE,
)
_CLASS_YEAR = re.compile(
    r"\b(?:freshm[ae]n|sophomores?|juniors?|(?<!as a )seniors?)\b(?=[^.]{0,40}\b(?:students?|standing|year)\b)"
    r"|\brising (?:sophomore|junior|senior)s?\b|\bpenultimate[ -]year\b",
    re.IGNORECASE,
)
_FOUR_YEAR = re.compile(
    r"\b(?:four|4)[ -]year (?:college|degree|university|program|or)\b", re.IGNORECASE
)
_NOT_ELIGIBLE = re.compile(
    r"\b(?:not|aren't|isn't) (?:be )?eligible\b|\bineligible\b", re.IGNORECASE
)
# Every rule below needs one of these, and checking for them is cheap.
_LEVEL = re.compile(
    r"(?i:ph\.?d|doctora|master|bachelor|grad|degree|mba|freshm|sophomore|junior|senior|penultimate"
    r"|(?:four|4)[ -]year|\b[bm]\.?s)"
)
_POSTDOC = re.compile(r"\bpost-?\s?doc", re.IGNORECASE)

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        1,
    )
    for name in names
}
_SEASONS = {
    "spring": (1, 6),
    "summer": (5, 9),
    "fall": (9, 12),
    "autumn": (9, 12),
    "winter": (1, 12),
}
_NAMES = "|".join(sorted((*_MONTHS, *_SEASONS), key=len, reverse=True))
_DATE = re.compile(
    rf"\b(?:(?P<name>{_NAMES})\.?(?:\s*(?:/|or|and|&)\s*(?P<second>{_NAMES})\.?)?"
    rf"(?:\s+\d{{1,2}})?,?\s+(?:of\s+)?)?(?P<year>20[2-3]\d)\b",
    re.IGNORECASE,
)
_GRADUATION = re.compile(
    r"\bgraduat(?:e|es|ing)\b(?!\s+(?:degree|program|school|student|level|course|research|studies))"
    r"|\bgraduation\b|\bclass of\b|\bdegree completion\b",
    re.IGNORECASE,
)
_REACH = 130
MAX_DATE_DISTANCE = 60
PREFERENCE_REACH = 90
_UP_TO = re.compile(
    r"\b(?:by|no later than|not later than|on or before|until)\s*(?:the end of\s*)?$",
    re.IGNORECASE,
)
_BEFORE = re.compile(r"(?<!or )(?<!not )\b(?:before|prior to)\s*$", re.IGNORECASE)
_FROM = re.compile(
    r"\b(?:after|no earlier than|not before|on or after|from|since|starting)\s*$",
    re.IGNORECASE,
)
_ONWARD = re.compile(
    r"^\s*(?:or (?:later|after|beyond)|and (?:later|beyond)|onwards?)", re.IGNORECASE
)
_PREFERRED_NEXT = re.compile(
    r"^[\s,(]*(?:is |are |strongly |highly )*preferred", re.IGNORECASE
)
_OR_EARLIER = re.compile(r"^\s*or (?:earlier|before|sooner)", re.IGNORECASE)


@dataclass(frozen=True)
class GraduationWindow:
    earliest: date | None
    latest: date | None

    def allows(self, graduation: date) -> bool:
        return (self.earliest is None or graduation >= self.earliest) and (
            self.latest is None or graduation <= self.latest
        )


@dataclass(frozen=True)
class Requirements:
    """What a posting clearly states: no degrees or no window means it does not say."""

    degrees: frozenset[Degree]
    graduation: GraduationWindow | None

    def admits_degree(self, degree: Degree) -> bool:
        held = {degree, Degree.masters} if degree is Degree.mba else {degree}
        return not self.degrees or not self.degrees.isdisjoint(held)

    def admits_graduation(self, year: int) -> bool:
        # Without a month, any month of the year that fits is enough.
        window = self.graduation
        return window is None or (
            (window.earliest is None or window.earliest.year <= year)
            and (window.latest is None or window.latest.year >= year)
        )


NONE_STATED = Requirements(frozenset(), None)


def normalize(text: str) -> str:
    text = text.replace("\u2019", "'")
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    return text


def read_requirements(title: str, description: str) -> Requirements:
    sentences = _SENTENCE_END.split(normalize(description))
    degrees, graduation = _accepted_degrees(title, sentences), _graduation(sentences)
    # Most postings say nothing; sharing one object keeps the full-time index small.
    return Requirements(degrees, graduation) if degrees or graduation else NONE_STATED


def _accepted_degrees(title: str, sentences: list[str]) -> frozenset[Degree]:
    if _POSTDOC.search(title):
        return frozenset({Degree.phd})
    if named := degrees_named(normalize(title)):
        return frozenset(named)
    accepted: set[Degree] = set()
    for sentence in sentences:
        if (
            not _LEVEL.search(sentence)
            or not _APPLICANT.search(sentence)
            or _NOT_ELIGIBLE.search(sentence)
        ):
            continue
        required = _drop_optional(sentence)
        found = degrees_named(required)
        if _CLASS_YEAR.search(required) or _FOUR_YEAR.search(required):
            found.add(Degree.bachelors)
        if (_ADVANCED.search(required) and not found) or (
            found and _GRADUATE.search(required)
        ):
            found |= {Degree.masters, Degree.phd}
        if {Degree.bachelors, Degree.phd} <= found:
            found.add(Degree.masters)
        if found and _OR_HIGHER.search(required):
            lowest = min((LEVELS.index(d) for d in found if d in LEVELS), default=0)
            found |= set(LEVELS[lowest:])
        if Degree.bachelors not in found and _only_preferred(required):
            continue
        accepted |= found
    return frozenset(accepted)


def _only_preferred(sentence: str) -> bool:
    # Lists lose their line breaks, so "preferred" further on may describe something else.
    mentions = [
        m for pattern in DEGREE_REGEXES.values() for m in pattern.finditer(sentence)
    ]
    if not mentions:
        return False
    last = max(mention.end() for mention in mentions)
    return bool(_OPTIONAL.search(sentence[last : last + PREFERENCE_REACH]))


def _drop_optional(sentence: str) -> str:
    # "Pursuing a BS, MS or PhD preferred" still accepts a BS, so only whole optional sentences
    # that state no pursuit are dropped.
    if _OPTIONAL.search(sentence) and not re.search(
        r"pursuing|enrolled|working toward|completed|required|must",
        sentence,
        re.IGNORECASE,
    ):
        return ""
    return sentence


def _graduation(sentences: list[str]) -> GraduationWindow | None:
    for sentence in sentences:
        for keyword in _GRADUATION.finditer(sentence):
            reach = sentence[keyword.end() : keyword.end() + _REACH]
            if window := _window_in(reach):
                return window
    return None


def _window_in(text: str) -> GraduationWindow | None:
    dates = list(_DATE.finditer(text))
    if not dates or dates[0].start() > MAX_DATE_DISTANCE:
        return None
    first = dates[0]
    run = [first]
    for following in dates[1:]:
        gap = text[run[-1].end() : following.start()]
        if not re.fullmatch(
            r"[\s,]*(?:-|–|—|to|and|or|through|/|&|,)?[\s,]*(?:or|and)?\s*", gap
        ):
            break
        run.append(following)
    spans = [_span(match) for match in run]
    earliest, latest = min(s[0] for s in spans), max(s[1] for s in spans)
    before, after = text[: first.start()], text[run[-1].end() :]
    if _PREFERRED_NEXT.match(after):
        return None
    if len(run) == 1:
        if _BEFORE.search(before):
            return GraduationWindow(None, earliest)
        if _UP_TO.search(before) or _OR_EARLIER.match(after):
            return GraduationWindow(None, latest)
        if _FROM.search(before) or _ONWARD.match(after):
            return GraduationWindow(earliest, None)
    elif _ONWARD.match(after):
        return GraduationWindow(earliest, None)
    return GraduationWindow(earliest, latest)


def _span(match: re.Match) -> tuple[date, date]:
    year = int(match["year"])
    months = [_month_range(name) for name in (match["name"], match["second"]) if name]
    if not months:
        months = [(1, 12)]
    first, last = min(m[0] for m in months), max(m[1] for m in months)
    return date(year, first, 1), date(year, last, 28)


def _month_range(name: str) -> tuple[int, int]:
    name = name.lower()
    if name in _SEASONS:
        return _SEASONS[name]
    return _MONTHS[name], _MONTHS[name]
