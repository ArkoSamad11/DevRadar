from dataclasses import dataclass

import httpx

LISTINGS_PATH = "dev/.github/scripts/listings.json"


@dataclass(frozen=True)
class ListSource:
    name: str
    url: str
    shortened_links: bool = False


@dataclass(frozen=True)
class Listing:
    company_name: str
    url: str


LIST_SOURCES = (
    ListSource(
        "simplify",
        f"https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/{LISTINGS_PATH}",
    ),
    ListSource(
        "vansh",
        f"https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/{LISTINGS_PATH}",
        shortened_links=True,
    ),
)


async def fetch_listings(
    client: httpx.AsyncClient, source: ListSource
) -> list[Listing]:
    response = await client.get(source.url)
    response.raise_for_status()
    return [
        Listing(row["company_name"].strip(), row["url"].strip())
        for row in response.json()
        if row.get("company_name") and row.get("url")
    ]
