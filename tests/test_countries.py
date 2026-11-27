import pytest

from devradar.ingestion import ashby, lever
from devradar.ingestion.countries import countries_in, country_code, posting_countries
from tests.test_ashby import raw_job
from tests.test_lever import raw_posting


@pytest.mark.parametrize(
    "location, expected",
    [
        ("New York, NY", {"US"}),
        ("Remote - United States", {"US"}),
        ("Seattle, WA, US", {"US"}),
        ("San Francisco", {"US"}),
        ("Athens, Georgia", {"US"}),
        ("Albuquerque, New Mexico", {"US"}),
        ("Portland, OR", {"US"}),
        ("Toronto, ON", {"CA"}),
        ("London, Ontario", {"CA"}),
        ("London", {"GB"}),
        ("London, England, United Kingdom", {"GB"}),
        ("Sydney, New South Wales", {"AU"}),
        ("Bangalore, IN", {"IN"}),
        ("Berlin, DE", {"DE"}),
        ("Wilmington, DE", {"US"}),
        ("Hong Kong, Singapore", {"HK", "SG"}),
        ("New York, NY | London", {"US", "GB"}),
        ("London OR Brussels", {"GB", "BE"}),
        ("Atlanta, GA, New York, NY, or Remote, USA", {"US"}),
        ("London, Paris, New York", {"GB", "FR", "US"}),
        ("Hong Kong, Singapore, Shanghai, Beijing", {"HK", "SG", "CN"}),
        ("Paris, TX", {"US"}),
        ("Medellin, CO", {"CO"}),
        ("Toronto, Ontario, Canada", {"CA"}),
        ("Remote", set()),
        ("Hybrid", set()),
        ("Europe", set()),
        ("North America", set()),
        ("Latin America", set()),
        ("Cambridge", set()),
        ("Manchester", set()),
    ],
)
def test_countries_in(location, expected):
    assert countries_in(location) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("US", "US"),
        ("United States", "US"),
        ("uk", "GB"),
        ("Canada", "CA"),
        ("Global", None),
    ],
)
def test_country_code(name, expected):
    assert country_code(name) == expected


def test_a_reported_country_beats_reading_the_text():
    assert posting_countries(("Cambridge", "Remote"), {"Cambridge": "GB"}) == ["GB"]
    assert posting_countries(("Cambridge", "Toronto"), {}) == ["CA"]


def test_ashby_reports_a_country_for_each_location():
    posting = ashby.parse_posting(
        raw_job(
            location="Cambridge",
            address={"postalAddress": {"addressCountry": "United Kingdom"}},
            secondaryLocations=[
                {
                    "location": "Remote-Taipei",
                    "address": {"postalAddress": {"addressCountry": "Taiwan"}},
                },
                {"location": "Boston"},
            ],
        )
    )
    assert posting.country_hints == {"Cambridge": "GB", "Remote-Taipei": "TW"}
    assert posting_countries(posting.locations, posting.country_hints) == [
        "GB",
        "TW",
        "US",
    ]


def test_lever_country_applies_to_the_main_location_only():
    posting = lever.parse_posting(raw_posting(country="US"))
    assert posting.country_hints == {"Mountain View, CA": "US"}
    assert posting_countries(posting.locations, posting.country_hints) == ["JP", "US"]
