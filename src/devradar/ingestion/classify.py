import re

INTERNSHIP = re.compile(
    r"\b(interns?|internships?|co-?ops?|apprentice(ship)?s?)\b", re.IGNORECASE
)
REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)

TITLE_CATEGORIES = {
    "quant": r"\bquant(itative)?\b|\btrad(er|ers|ing)\b|market[- ]mak",
    "operations_research": r"operations research|optimization|decision scien",
    "math": r"\bmath(ematics|ematician|ematical)?\b|statistic|actuar|probabilit",
    "ml_ai": (
        r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|\bllms?\b"
        r"|computer vision|\bnlp\b|applied scien|research scien"
    ),
    "data_science": r"data scien|data analy|analytics|data engineer",
    "systems": (
        r"\bsystems?\b|embedded|firmware|kernel|operating system|distributed|infrastructure"
        r"|\binfra\b|compiler|\bsre\b|site reliability|devops|platform|network|\bhpc\b"
    ),
    "software": (
        r"software|developer|\bswe\b|back[- ]?end|front[- ]?end|full[- ]?stack|mobile"
        r"|\bios\b|android|\bweb\b|programmer"
    ),
}
DESCRIPTION_CATEGORIES = {
    "operations_research": (
        r"operations research|mixed[- ]integer|linear programming|integer programming"
        r"|combinatorial optimization|stochastic optimization"
    ),
    "math": r"\bmathematicians?\b|pure mathematics|applied mathematics|mathematical modell?ing",
}

_TITLE_PATTERNS = {
    slug: re.compile(p, re.IGNORECASE) for slug, p in TITLE_CATEGORIES.items()
}
_DESCRIPTION_PATTERNS = {
    slug: re.compile(p, re.IGNORECASE) for slug, p in DESCRIPTION_CATEGORIES.items()
}


def is_internship(title: str, employment_type: str | None = None) -> bool:
    return bool(
        INTERNSHIP.search(title)
        or (employment_type and INTERNSHIP.search(employment_type))
    )


def categorize(title: str, description: str | None = None) -> set[str]:
    categories = {
        slug for slug, pattern in _TITLE_PATTERNS.items() if pattern.search(title)
    }
    if description:
        categories |= {
            slug
            for slug, pattern in _DESCRIPTION_PATTERNS.items()
            if pattern.search(description)
        }
    return categories


def is_remote(location: str) -> bool:
    return bool(REMOTE.search(location))
