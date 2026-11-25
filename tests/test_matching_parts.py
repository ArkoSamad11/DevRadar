import asyncio
import time

import pytest

from devradar.matching import pdf
from devradar.matching.eligibility import (
    Degree,
    degrees_named,
    mentions_work_authorization,
    terms_named,
)
from devradar.matching.limits import RateLimiter
from devradar.matching.pdf import UnreadablePdf, extract_text
from devradar.matching.skills import SKILLS, find_skills


def make_pdf(*lines):
    text = " ".join(f"({line}) Tj 0 -14 Td" for line in lines)
    stream = f"BT /F1 12 Tf 72 720 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, content)
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    return body + b"startxref\n%d\n%%%%EOF" % xref


@pytest.mark.anyio
async def test_extract_text_reads_a_pdf():
    text = await extract_text(make_pdf("Python and PostgreSQL", "Distributed systems"))
    assert "Python and PostgreSQL" in text and "Distributed systems" in text


@pytest.mark.anyio
@pytest.mark.parametrize("content", [b"", b"not a pdf", b"%PDF-1.4 truncated"])
async def test_extract_text_rejects_unreadable_files(content):
    with pytest.raises(UnreadablePdf):
        await extract_text(content)


@pytest.mark.anyio
async def test_a_stuck_parser_is_killed(monkeypatch):
    monkeypatch.setattr(pdf, "WALL_SECONDS", 0.05)
    started = time.monotonic()
    with pytest.raises(UnreadablePdf, match="too long"):
        await extract_text(make_pdf("slow"))
    assert time.monotonic() - started < 2


@pytest.mark.anyio
async def test_cancelled_request_does_not_leave_a_parser_running():
    task = asyncio.create_task(extract_text(make_pdf("x")))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Software Engineer Intern", set()),
        ("AI Research Intern, PhD", {Degree.phd}),
        ("Quant Intern (Ph.D.)", {Degree.phd}),
        ("Research Intern (BS/MS/PhD)", {Degree.bachelors, Degree.masters, Degree.phd}),
        ("Product Manager, MBA Intern", {Degree.mba}),
        ("User Research Intern - Master's", {Degree.masters}),
        ("MS SQL Intern", set()),
        ("Undergraduate Intern, Systems", {Degree.bachelors}),
    ],
)
def test_degrees_named(title, expected):
    assert degrees_named(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Intern - Summer 2027", {"summer 2027"}),
        ("2027 Summer Intern", {"summer 2027"}),
        ("Computer Vision Intern, Fall/Winter 2026", {"fall 2026", "winter 2026"}),
        ("Autumn 2026 Co-op", {"fall 2026"}),
        ("Software Engineer Intern 2027", set()),
        ("Summer Intern", set()),
    ],
)
def test_terms_named(title, expected):
    assert terms_named(title) == expected


def test_mentions_work_authorization():
    assert mentions_work_authorization("Must be a U.S. citizen.")
    assert mentions_work_authorization("Ability to obtain a security clearance")
    assert not mentions_work_authorization(
        "We sponsor visas. Citizens of the web welcome."
    )


def test_find_skills():
    text = "Python, C++, C#, Go, Node.js, postgres, REST APIs and CI/CD; SystemVerilog; k8s"
    assert find_skills(text) == {
        "Python", "C++", "C#", "Go", "Node.js", "PostgreSQL", "REST APIs", "CI/CD", "Verilog",
        "Kubernetes",
    }  # fmt: skip


@pytest.mark.parametrize(
    "text", ["let's go to market", "trust and rusty", "excellent", "digital"]
)
def test_find_skills_ignores_lookalike_words(text):
    assert find_skills(text) == set()


def test_skill_names_are_found_in_themselves():
    missed = [name for name in SKILLS if name not in find_skills(f"{name},")]
    assert set(missed) <= {"Optimization", "Embedded", "Networking", "Spring"}


def test_rate_limiter_limits_each_client_and_the_total():
    now = [0.0]
    limiter = RateLimiter(per_client=2, total=3, window=60, clock=lambda: now[0])
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [
        True,
        True,
        False,
    ]
    assert [limiter.allow("b"), limiter.allow("c")] == [True, False]
    now[0] = 61
    assert limiter.allow("a")
