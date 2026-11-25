from datetime import date

import pytest

from devradar.matching.eligibility import Degree
from devradar.matching.requirements import (
    NONE_STATED,
    GraduationWindow,
    Requirements,
    read_requirements,
)

B, M, P = Degree.bachelors, Degree.masters, Degree.phd


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Currently pursuing a bachelor’s, master’s, or doctoral degree in physics.",
            {B, M, P},
        ),
        (
            "Qualifications Currently pursuing a B.S. or M.S. in Computer Science.",
            {B, M},
        ),
        ("Pursuing Masters in Computer Science or related field.", {M}),
        ("Currently pursuing a Master’s degree or PhD in: Computer Science.", {M, P}),
        (
            "We are seeking a PhD research intern to join our team. Qualifications Ph.D. student.",
            {P},
        ),
        ("You’re currently pursuing a PhD in computer science.", {P}),
        ("Pursuing a bachelor's degree or higher in Computer Science.", {B, M, P}),
        ("Pursuing B.S., preferably M.S. or Ph.D in engineering.", {B, M, P}),
        ("Currently enrolled in an MS or PhD program in machine learning.", {M, P}),
        ("This is designed for PhD students who want a 12-week internship.", {P}),
        ("Currently pursuing a degree in Computer Science.", set()),
        ("Our founder has a PhD from Harvard and built models at Meta.", set()),
        ("Masters and PhD-level engineers are a critical part of our company.", set()),
        (
            "Strong Python skills. Graduate degrees are preferred, but not a must.",
            set(),
        ),
        ("You will work with MS Office and MS SQL.", set()),
        ("A PhD is a plus.", set()),
        (
            (
                "Pursuing a Master's or PhD in statistics, operations research, applied math, or a"
                " quantitative science Solid grounding in Bayesian inference Rust is preferred"
            ),
            {M, P},
        ),
        ("Pursuing a Master's or PhD in Computer Science preferred.", set()),
        ("Student actively pursuing a four year or advanced degree in CS.", {B, M, P}),
        (
            "If you have already graduated from a four-year university, apply elsewhere.",
            set(),
        ),
        ("Junior, senior, or master's student in Statistics.", {B, M}),
        ("Enrolled in a four-year undergraduate, graduate, or PhD program.", {B, M, P}),
        (
            "Must be enrolled in an accredited undergraduate or graduate degree program.",
            {B, M, P},
        ),
        (
            "Currently an undergraduate, or graduate student pursuing a degree in CS.",
            {B, M, P},
        ),
        (
            "For enrolled students in all levels from bachelor's degree to PhD",
            {B, M, P},
        ),
        ("Advanced degrees from top universities, majoring in statistics.", {M, P}),
        (
            "Master's students. Students enrolled in PhD or MBA programs are not eligible.",
            {M},
        ),
        ("Bachelor's student, or a recent graduate, in Computer Science.", {B}),
        ("", set()),
    ],
)
def test_accepted_degrees_in_descriptions(description, expected):
    assert read_requirements("Machine Learning Intern", description).degrees == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Research Intern, PhD", {P}),
        ("Postdoctoral AI Scientist", {P}),
        ("Post-doc Researcher", {P}),
        ("Research Intern (BS/MS/PhD)", {B, M, P}),
    ],
)
def test_a_degree_in_the_title_decides(title, expected):
    assert (
        read_requirements(title, "Currently pursuing a Bachelor's degree.").degrees
        == expected
    )


@pytest.mark.parametrize(
    ("description", "earliest", "latest"),
    [
        (
            "with an expected graduation date between December 2027 and May 2028.",
            date(2027, 12, 1),
            date(2028, 5, 28),
        ),
        (
            "Expected to graduate between Fall 2027 and Summer 2028",
            date(2027, 9, 1),
            date(2028, 9, 28),
        ),
        (
            "an expected graduation date of May 2027, December 2027, or May 2028, OR recently done",
            date(2027, 5, 1),
            date(2028, 5, 28),
        ),
        ("Expected to graduate by Summer 2028", None, date(2028, 9, 28)),
        ("Must graduate by August 2028. GPA of 3.5", None, date(2028, 8, 28)),
        (
            "an expected graduation date between Dec 2027 - 2029.",
            date(2027, 12, 1),
            date(2029, 12, 28),
        ),
        (
            "with a graduation date in Fall 2027 or Spring 2028",
            date(2027, 9, 1),
            date(2028, 6, 28),
        ),
        ("Must graduate before Summer 2028.", None, date(2028, 5, 1)),
        ("graduating in 2028 or later", date(2028, 1, 1), None),
        ("Graduating no earlier than December 2027", date(2027, 12, 1), None),
        ("Class of 2028 students", date(2028, 1, 1), date(2028, 12, 28)),
        ("Graduation in Dec/May 2028", date(2028, 5, 1), date(2028, 12, 28)),
    ],
)
def test_graduation_window(description, earliest, latest):
    assert read_requirements("Intern", description).graduation == GraduationWindow(
        earliest, latest
    )


@pytest.mark.parametrize(
    "description",
    [
        "Interns may be considered for a full-time role upon graduation.",
        "Graduate students welcome. The program runs June 2027 to August 2027.",
        "Computer Science, Economics or related. Class of 2028, preferred",
        "Recent graduates are welcome. Our company was founded in 2021.",
        "We are graduating our product from beta in a process that will take a while and finish 2027",
        "",
    ],
)
def test_no_graduation_window_is_invented(description):
    assert read_requirements("Intern", description).graduation is None


def test_window_allows():
    window = GraduationWindow(date(2027, 12, 1), date(2028, 5, 28))
    assert window.allows(date(2028, 5, 1)) and not window.allows(date(2029, 5, 1))
    assert GraduationWindow(None, date(2028, 9, 28)).allows(date(2026, 5, 1))
    assert GraduationWindow(date(2028, 1, 1), None).allows(date(2031, 5, 1))


def test_unstated_requirements_admit_everyone_and_share_one_object():
    unstated = read_requirements("Software Intern", "We build things.")
    assert unstated is NONE_STATED
    assert all(unstated.admits_degree(degree) for degree in Degree)
    assert unstated.admits_graduation(date(2029, 5, 1))


def test_an_mba_counts_as_a_masters_but_not_the_reverse():
    assert Requirements(frozenset({Degree.masters}), None).admits_degree(Degree.mba)
    assert not Requirements(frozenset({Degree.mba}), None).admits_degree(Degree.masters)
