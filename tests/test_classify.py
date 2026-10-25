import pytest

from devradar.ingestion.classify import categorize, is_internship, is_remote


@pytest.mark.parametrize(
    ("title", "employment_type", "expected"),
    [
        ("Software Engineer Intern, Implant", None, True),
        ("Summer 2027 Internship - Quant Research", None, True),
        ("Data Science Co-op (Fall 2026)", None, True),
        ("Co-Op, Perception R&D", None, True),
        ("Software Engineering Apprentice", None, True),
        ("Internal Tools Engineer", None, False),
        ("International Tax Analyst", None, False),
        ("Research Assistant", "Full-time Intern", True),
        ("Software Engineer", "Full-time", False),
    ],
)
def test_is_internship(title, employment_type, expected):
    assert is_internship(title, employment_type) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Software Engineer Intern", {"software"}),
        ("Mechanical Engineering Intern", set()),
        ("ML Infrastructure Intern", {"ml_ai", "systems"}),
        ("Quantitative Trader Intern", {"quant"}),
        ("Embedded Firmware Engineer Intern", {"systems"}),
        ("Data Scientist Intern - Analytics", {"data_science"}),
        ("Supply Chain Optimization Intern", {"operations_research"}),
        ("Statistics & Probability Research Intern", {"math"}),
        ("AI Research Scientist Intern", {"ml_ai"}),
        ("Product Marketing Intern", set()),
    ],
)
def test_categorize_by_title(title, expected):
    assert categorize(title) == expected


def test_description_reveals_operations_research_and_math_only():
    description = (
        "You will build mixed-integer models. A degree in applied mathematics is "
        "preferred. Experience with data and software is a plus."
    )
    assert categorize("Analyst Intern", description) == {"operations_research", "math"}


@pytest.mark.parametrize(
    ("location", "expected"),
    [("Remote - US", True), ("New York, NY (Remote)", True), ("Fremont, CA", False)],
)
def test_is_remote(location, expected):
    assert is_remote(location) is expected
