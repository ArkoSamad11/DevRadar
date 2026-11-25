import json

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from devradar.roles import dataset, train
from devradar.roles.dataset import Example, deduplicate
from devradar.roles.model import METADATA_PATH, RoleModel, load_model, save_model
from devradar.roles.text import ROLES, tokenize

RESUMES = {
    "software": "Python, Go, PostgreSQL, Docker, FastAPI, REST APIs. Built backend web services"
    " and a distributed data ingestion pipeline. Coursework: databases, algorithms.",
    "ml_ai": "PyTorch, transformers, large language models, fine tuning, computer vision."
    " Trained neural networks.",
    "data_science": "SQL, Tableau, A/B testing, regression, pandas, dashboards, statistical"
    " analysis, experimentation.",
    "systems": "C, C++, Linux kernel, device drivers, firmware, RTOS, microcontrollers.",
    "security": "Penetration testing, incident response, vulnerability assessment, cryptography.",
    "quant": "Probability, stochastic processes, time series, Monte Carlo simulation, trading"
    " competition, market making game.",
}


def example(company_id, title, description, source="ashby", internship=True):
    return Example(company_id, title, description, source, internship)


def test_tokenize_keeps_programming_languages_and_drops_encoded_images():
    assert tokenize("C++, C#, .NET, Node.js and R.") == [
        "c++",
        "c#",
        ".net",
        "node.js",
        "and",
        "r",
    ]
    assert tokenize("data " + "iVBORw0KGgo" * 20) == ["data"]


def test_deduplicate_drops_short_and_reposted_descriptions():
    body = "Build reliable systems. " * 100
    examples = [
        example(1, "Intern, NYC", "New York. " + body),
        example(1, "Intern, SF", "San Francisco. " + body),
        example(2, "Intern", "Elsewhere. " + body),
        example(3, "Intern", "too short"),
    ]
    assert [e.company_id for e in deduplicate(examples)] == [1, 2]


def test_company_folds_never_split_a_company():
    companies = np.repeat(np.arange(40), 3)
    folds = train.company_folds(companies, seed=1)
    assert all(len(set(folds[companies == c])) == 1 for c in range(40))
    assert set(folds) == set(range(train.FOLDS))


def test_terms_used_by_few_companies_are_dropped(monkeypatch):
    monkeypatch.setattr(train, "MIN_DOCUMENTS", 1)
    monkeypatch.setattr(train, "MIN_COMPANIES", 2)
    monkeypatch.setattr(train, "MAX_DOCUMENT_SHARE", 1.0)
    corpus = train.Corpus(
        [
            example(1, "Intern", "python brandname brandname"),
            example(1, "Intern", "python brandname"),
            example(2, "Intern", "python rust"),
            example(3, "Intern", "rust"),
        ]
    )
    kept, _ = train.select_terms(corpus.frequencies, corpus.companies)
    assert sorted(corpus.terms[kept]) == ["python", "rust"]


def test_numpy_inference_matches_scikit_learn(monkeypatch, tmp_path):
    monkeypatch.setattr(train, "MIN_DOCUMENTS", 1)
    monkeypatch.setattr(train, "MIN_COMPANIES", 1)
    monkeypatch.setattr(train, "MAX_DOCUMENT_SHARE", 1.0)
    rng = np.random.default_rng(0)
    words = ["python", "c++", "trading", "kernel", "pytorch", "sql", "security", "go"]
    titles = [
        "Software",
        "Machine Learning",
        "Data Science",
        "Firmware",
        "Security",
        "Quant",
    ]
    examples = [
        example(i, f"{titles[i % 6]} Intern", " ".join(rng.choice(words, 30)))
        for i in range(60)
    ]
    corpus = train.Corpus(examples)
    kept, idf = train.select_terms(corpus.frequencies, corpus.companies)
    features = train.transform(corpus.frequencies, kept, idf)
    fitted = [
        LogisticRegression().fit(features, corpus.labels[:, r])
        for r in range(len(ROLES))
    ]
    model = RoleModel(
        vocabulary={term: i for i, term in enumerate(corpus.terms[kept].tolist())},
        idf=idf,
        weights=np.vstack([m.coef_[0] for m in fitted]),
        intercepts=np.array([m.intercept_[0] for m in fitted]),
        slopes=np.ones(len(ROLES)),
        offsets=np.zeros(len(ROLES)),
    )
    save_model(model, {}, tmp_path)
    loaded = load_model(tmp_path / "model.npz")
    expected = np.column_stack([m.predict_proba(features)[:, 1] for m in fitted])
    actual = np.array([list(loaded.scores(e.description).values()) for e in examples])
    np.testing.assert_allclose(actual, expected, atol=1e-6)


@pytest.mark.parametrize("role", ROLES)
def test_shipped_model_recognizes_a_clear_resume(role):
    scores = load_model().scores(RESUMES[role])
    assert max(scores, key=scores.get) == role


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Nursing student. Patient care, vital signs, clinical rotations, CPR certified.",
    ],
)
def test_shipped_model_stays_unsure_about_unrelated_text(text):
    assert max(load_model().scores(text).values()) < 0.2


def test_explanations_quote_words_from_the_text():
    terms = [term for term, _ in load_model().explain(RESUMES["quant"], "quant")]
    assert terms and set(terms) <= set(tokenize(RESUMES["quant"]))


def test_shipped_model_is_small():
    model = load_model()
    arrays = (model.idf, model.weights, model.intercepts, model.slopes, model.offsets)
    assert sum(a.nbytes for a in arrays) < 2_000_000
    assert max(map(len, model.vocabulary)) <= 30


def test_shipped_model_meets_the_quality_floor():
    metadata = json.loads(METADATA_PATH.read_text())
    evaluation = metadata["evaluation"]
    assert evaluation["mean_average_precision"] >= 0.63
    assert evaluation["precision_at_10"] >= 0.80
    assert evaluation["expected_calibration_error_after"] <= 0.03


def test_cache_round_trip(monkeypatch, tmp_path):
    body = "Describe the work in detail. " * 20
    stored = [example(1, "Software Engineer Intern", body)]
    monkeypatch.setattr(dataset, "stored_internships", lambda conninfo: stored)

    async def fetch(conninfo):
        return [example(2, "Data Scientist", body, "lever", internship=False)]

    monkeypatch.setattr(dataset, "fetch_full_time", fetch)
    cache = tmp_path / "cache" / "examples.json.gz"
    first = dataset.load_examples(cache=cache)
    monkeypatch.setattr(
        dataset, "stored_internships", lambda conninfo: pytest.fail("refetched")
    )
    assert dataset.load_examples(cache=cache) == first
    assert [(e.source, e.is_internship, e.roles) for e in first] == [
        ("ashby", True, {"software"}),
        ("lever", False, {"data_science"}),
    ]
