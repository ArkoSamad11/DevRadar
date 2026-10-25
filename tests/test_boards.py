import pytest

from devradar.discovery.boards import BoardRef, company_slug, parse_board


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://job-boards.greenhouse.io/samsungresearchamericainternship/jobs/8438402002",
            BoardRef("greenhouse", "samsungresearchamericainternship"),
        ),
        (
            "https://boards.greenhouse.io/transcarent/jobs/5808306004",
            BoardRef("greenhouse", "transcarent"),
        ),
        (
            "https://job-boards.eu.greenhouse.io/speechmatics/jobs/4834542101",
            BoardRef("greenhouse", "speechmatics"),
        ),
        (
            "https://job-boards.greenhouse.io/Sierra/jobs/1?gh_jid=1",
            BoardRef("greenhouse", "sierra"),
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=stripe&token=123",
            BoardRef("greenhouse", "stripe"),
        ),
        ("https://boards.greenhouse.io/embed/job_app?token=7713841003", None),
        (
            "https://jobs.lever.co/bluerocktx/73c7b9e2-03c6-4ab3-8ae1-6654c19fa41e/apply",
            BoardRef("lever", "bluerocktx"),
        ),
        (
            "https://jobs.eu.lever.co/cirrus/103a6cd2-74ef-495f-ab73-9a56dab653cd/apply",
            BoardRef("lever", "cirrus", "eu"),
        ),
        (
            "https://jobs.ashbyhq.com/circleback/2bb6be67-d1a8-42f7-bb1b-64ee36bf613f/application?embed=true",
            BoardRef("ashby", "circleback"),
        ),
        (
            "https://cadence.wd1.myworkdayjobs.com/Univ_Careers/job/SAN-JOSE/SSG-Design-Engineering-Intern_R52641-2",
            BoardRef("workday", "cadence/wd1/Univ_Careers"),
        ),
        (
            "https://pae.wd1.myworkdayjobs.com/en-US/amentum_careers/job/US-CA/Rotational-Engineering-Intern_R0154978",
            BoardRef("workday", "pae/wd1/amentum_careers"),
        ),
        ("https://careers.cisco.com/global/en/job/2008901", None),
        ("https://www.stripe.com/jobs/listing/intern?gh_jid=123", None),
        ("https://jobs.lever.co/", None),
        ("not a url", None),
    ],
)
def test_parse_board(url, expected):
    assert parse_board(url) == expected


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Samsung Research America", "samsung-research-america"),
        ("Lowe's Companies, Inc.", "lowes-companies"),
        ("Stripe, Inc", "stripe"),
        ("DV Trading LLC", "dv-trading"),
        ("Co", "co"),
        ("  Jane   Street  ", "jane-street"),
    ],
)
def test_company_slug(name, slug):
    assert company_slug(name) == slug
