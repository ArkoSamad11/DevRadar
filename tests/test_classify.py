import csv
from pathlib import Path

import pytest

from devradar.ingestion.classify import categorize, is_internship, is_remote

SAMPLE = Path(__file__).parent / "fixtures" / "classifier_sample.csv"


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
        ("Water Infrastructure Engineering Intern", set()),
        ("Data Science Co-Op (Embedded Science Team)", {"data_science"}),
        ("Embedded Software Engineer Intern", {"systems", "software"}),
        ("Data Center Engineer Intern", {"systems"}),
        ("Cybersecurity Internship", {"security"}),
        ("Hardware Security Engineer Intern", {"security"}),
        ("Securities Operations Intern", set()),
        ("AI & Management Consulting Intern (Value Engineering)", set()),
        ("Recruiting Intern - AI & Automation", set()),
        ("Sales Engineer Intern - Cloud Platform", {"systems"}),
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


def test_holdout_sample_meets_quality_floor():
    with SAMPLE.open() as f:
        rows = [row for row in csv.DictReader(f) if row["split"] == "holdout"]
    tp = fp = fn = 0
    for row in rows:
        expected = set(filter(None, row["categories"].split(";")))
        predicted = categorize(row["title"])
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    assert tp / (tp + fp) >= 0.85
    assert tp / (tp + fn) >= 0.90
