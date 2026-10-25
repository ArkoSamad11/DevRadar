import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

GREENHOUSE_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
}
LEVER_HOSTS = {"jobs.lever.co": "global", "jobs.eu.lever.co": "eu"}
WORKDAY_HOST = re.compile(r"^([\w-]+)\.(wd\d+)\.myworkdayjobs\.com$")
LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "llp",
    "lp",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "plc",
    "gmbh",
}


@dataclass(frozen=True)
class BoardRef:
    ats: str
    token: str
    api_region: str = "global"


def parse_board(url: str) -> BoardRef | None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    segments = [segment for segment in parsed.path.split("/") if segment]

    if host in GREENHOUSE_HOSTS:
        if segments[:1] == ["embed"]:
            token = parse_qs(parsed.query).get("for", [""])[0]
        else:
            token = segments[0] if segments else ""
        return BoardRef("greenhouse", token.lower()) if token else None

    if host in LEVER_HOSTS:
        return BoardRef("lever", segments[0], LEVER_HOSTS[host]) if segments else None

    if host == "jobs.ashbyhq.com":
        return BoardRef("ashby", segments[0].lower()) if segments else None

    if match := WORKDAY_HOST.match(host):
        if segments and LOCALE.match(segments[0]):
            segments = segments[1:]
        if not segments:
            return None
        tenant, shard = match.groups()
        return BoardRef("workday", f"{tenant}/{shard}/{segments[0]}")

    return None


def company_slug(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’]", "", name.lower())).split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "-".join(words)
