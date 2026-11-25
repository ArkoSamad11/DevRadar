"""Train the role model. Needs scikit-learn, which is a development dependency only."""

import argparse
import json
from datetime import UTC, datetime

import numpy as np
import scipy.sparse as sp
from joblib import Parallel, delayed
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score

from devradar.roles.dataset import Example, load_examples
from devradar.roles.model import RoleModel, save_model
from devradar.roles.text import ROLES, tokenize

MIN_DOCUMENTS = 5
# Words from one employer's boilerplate say nothing about a role.
MIN_COMPANIES = 5
MAX_DOCUMENT_SHARE = 0.3
MAX_TERMS = 20_000
FOLDS = 5
SELECTION_SEED = 0
REPORT_SEEDS = (1, 2, 3)
CANDIDATE_C = (1, 3, 10, 30)


class Corpus:
    def __init__(self, examples: list[Example]) -> None:
        vectorizer = CountVectorizer(
            tokenizer=tokenize,
            lowercase=False,
            token_pattern=None,
            min_df=MIN_DOCUMENTS,
        )
        counts = vectorizer.fit_transform(e.description for e in examples).tocsr()
        counts.data = 1 + np.log(counts.data.astype(np.float64))
        self.frequencies = counts
        self.terms = vectorizer.get_feature_names_out()
        self.labels = np.array(
            [[role in e.roles for role in ROLES] for e in examples], dtype=int
        )
        self.companies = np.array([e.company_id for e in examples])
        self.internships = np.array([e.is_internship for e in examples])
        self.sources = np.array([e.source for e in examples])


def select_terms(
    frequencies: sp.csr_matrix, companies: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    n = frequencies.shape[0]
    documents = np.bincount(frequencies.indices, minlength=frequencies.shape[1])
    _, company_index = np.unique(companies, return_inverse=True)
    by_company = (
        sp.csr_matrix((np.ones(n), (company_index, np.arange(n)))) @ frequencies
    )
    company_counts = np.bincount(
        by_company.tocsr().indices, minlength=frequencies.shape[1]
    )
    kept = np.where(
        (documents >= MIN_DOCUMENTS)
        & (documents <= MAX_DOCUMENT_SHARE * n)
        & (company_counts >= MIN_COMPANIES)
    )[0]
    if len(kept) > MAX_TERMS:
        kept = np.sort(kept[np.argsort(-documents[kept], kind="stable")[:MAX_TERMS]])
    return kept, np.log((1 + n) / (1 + documents[kept])) + 1


def transform(
    frequencies: sp.csr_matrix, kept: np.ndarray, idf: np.ndarray
) -> sp.csr_matrix:
    weighted = frequencies[:, kept] @ sp.diags(idf)
    norms = np.sqrt(weighted.multiply(weighted).sum(axis=1)).A1
    normalized = (sp.diags(1 / np.where(norms == 0, 1, norms)) @ weighted).tocsr()
    normalized.sort_indices()
    return normalized


def fit_classifiers(features: sp.csr_matrix, labels: np.ndarray, c: float) -> list:
    def fit(column: int) -> LogisticRegression:
        model = LogisticRegression(
            C=c,
            class_weight="balanced",
            solver="liblinear",
            max_iter=1000,
            random_state=0,
        )
        return model.fit(features, labels[:, column])

    return Parallel(n_jobs=len(ROLES))(delayed(fit)(k) for k in range(len(ROLES)))


def company_folds(companies: np.ndarray, seed: int) -> np.ndarray:
    shuffled = np.random.default_rng(seed).permutation(np.unique(companies))
    fold_of = {company: i % FOLDS for i, company in enumerate(shuffled)}
    return np.array([fold_of[company] for company in companies])


def held_out_decisions(
    corpus: Corpus, usable: np.ndarray, c: float, seed: int
) -> np.ndarray:
    folds = company_folds(corpus.companies, seed)
    decisions = np.zeros(corpus.labels.shape)
    for fold in range(FOLDS):
        train = np.where((folds != fold) & usable)[0]
        test = np.where(folds == fold)[0]
        kept, idf = select_terms(corpus.frequencies[train], corpus.companies[train])
        models = fit_classifiers(
            transform(corpus.frequencies[train], kept, idf), corpus.labels[train], c
        )
        features = transform(corpus.frequencies[test], kept, idf)
        decisions[test] = np.column_stack(
            [m.decision_function(features) for m in models]
        )
    return decisions


def precision_at(labels: np.ndarray, scores: np.ndarray, k: int) -> float:
    return float(
        np.mean(
            [labels[np.argsort(-scores[:, r])[:k], r].mean() for r in range(len(ROLES))]
        )
    )


def evaluate(labels: np.ndarray, decisions: np.ndarray) -> dict[str, float]:
    has_role = labels.any(axis=1)
    best = decisions.argmax(axis=1)
    return {
        "mean_average_precision": float(
            np.mean(
                [
                    average_precision_score(labels[:, r], decisions[:, r])
                    for r in range(len(ROLES))
                ]
            )
        ),
        "macro_f1": float(f1_score(labels, decisions > 0, average="macro")),
        "precision_at_10": precision_at(labels, decisions, 10),
        "precision_at_20": precision_at(labels, decisions, 20),
        "best_guess_right": float(
            labels[np.arange(len(labels)), best][has_role].mean()
        ),
    }


def fit_calibration(
    decisions: np.ndarray, labels: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    fitted = [
        LogisticRegression(C=1e6).fit(decisions[:, [r]], labels[:, r])
        for r in range(len(ROLES))
    ]
    return np.array([m.coef_[0, 0] for m in fitted]), np.array(
        [m.intercept_[0] for m in fitted]
    )


def calibration_error(
    labels: np.ndarray, probabilities: np.ndarray, bins: int = 10
) -> float:
    errors = []
    for r in range(len(ROLES)):
        bucket = np.minimum((probabilities[:, r] * bins).astype(int), bins - 1)
        errors.append(
            sum(
                abs(
                    labels[bucket == b, r].mean() - probabilities[bucket == b, r].mean()
                )
                * (bucket == b).mean()
                for b in np.unique(bucket)
            )
        )
    return float(np.mean(errors))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-x))


def report_calibration(corpus: Corpus, decisions: np.ndarray) -> dict[str, float]:
    # Calibrate on half of the companies and measure on the other half.
    half = company_folds(corpus.companies, seed=99) % 2 == 0
    test = ~half & corpus.internships
    slopes, offsets = fit_calibration(decisions[half], corpus.labels[half])
    return {
        "expected_calibration_error_before": calibration_error(
            corpus.labels[test], sigmoid(decisions[test])
        ),
        "expected_calibration_error_after": calibration_error(
            corpus.labels[test], sigmoid(slopes * decisions[test] + offsets)
        ),
    }


def average(reports: list[dict[str, float]]) -> dict[str, float]:
    return {
        key: round(float(np.mean([r[key] for r in reports])), 3) for key in reports[0]
    }


def spread(reports: list[dict[str, float]]) -> dict[str, float]:
    return {
        key: round(float(np.ptp([r[key] for r in reports])), 3) for key in reports[0]
    }


def compare(corpus: Corpus, c: float) -> None:
    everything = np.ones(len(corpus.companies), dtype=bool)
    no_lever = corpus.sources != "lever"
    variants = {
        "internships only": corpus.internships,
        "internships only, no Lever": corpus.internships & no_lever,
        "internships + full-time": everything,
        "internships + full-time, no Lever": no_lever,
    }
    test = corpus.internships
    for name, usable in variants.items():
        reports = []
        for seed in REPORT_SEEDS:
            decisions = held_out_decisions(corpus, usable, c, seed)
            reports.append(evaluate(corpus.labels[test], decisions[test]))
        print(
            f"{name:36} train {usable.sum():6}  {average(reports)}  range {spread(reports)}"
        )


def train(corpus: Corpus, c: float) -> tuple[RoleModel, dict]:
    everything = np.ones(len(corpus.companies), dtype=bool)
    test = corpus.internships
    reports, calibrations, held_out = [], [], []
    for seed in REPORT_SEEDS:
        decisions = held_out_decisions(corpus, everything, c, seed)
        held_out.append(decisions)
        reports.append(evaluate(corpus.labels[test], decisions[test]))
        calibrations.append(report_calibration(corpus, decisions))
    slopes, offsets = fit_calibration(
        np.vstack(held_out), np.vstack([corpus.labels] * len(held_out))
    )

    kept, idf = select_terms(corpus.frequencies, corpus.companies)
    models = fit_classifiers(transform(corpus.frequencies, kept, idf), corpus.labels, c)
    model = RoleModel(
        vocabulary={term: i for i, term in enumerate(corpus.terms[kept].tolist())},
        idf=idf,
        weights=np.vstack([m.coef_[0] for m in models]),
        intercepts=np.array([m.intercept_[0] for m in models]),
        slopes=slopes,
        offsets=offsets,
    )
    metadata = {
        "trained_at": datetime.now(UTC).date().isoformat(),
        "algorithm": "TF-IDF + one logistic regression per role",
        "labels": "title rules in devradar.ingestion.classify",
        "features": "description text only",
        "regularization_c": c,
        "terms": len(kept),
        "examples": len(corpus.companies),
        "internship_examples": int(corpus.internships.sum()),
        "companies": len(np.unique(corpus.companies)),
        "examples_by_source": {
            s: int((corpus.sources == s).sum()) for s in np.unique(corpus.sources)
        },
        "positives": dict(zip(ROLES, corpus.labels.sum(axis=0).tolist(), strict=True)),
        "evaluation": {
            "measured_on": "internships of companies held out of training",
            "against": "title-rule labels, not human judgments of resume matches",
            "seeds": list(REPORT_SEEDS),
            **average(reports),
            **average(calibrations),
        },
    }
    return model, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="collect the examples again"
    )
    parser.add_argument(
        "--select-c", action="store_true", help="compare regularization strengths"
    )
    parser.add_argument(
        "--compare", action="store_true", help="compare training data choices"
    )
    parser.add_argument("--c", type=float, default=3.0)
    parser.add_argument(
        "--exclude-source", action="append", default=[], metavar="SOURCE"
    )
    args = parser.parse_args()

    examples = load_examples(args.refresh)
    corpus = Corpus([e for e in examples if e.source not in args.exclude_source])
    if args.select_c:
        everything, test = (
            np.ones(len(corpus.companies), dtype=bool),
            corpus.internships,
        )
        for c in CANDIDATE_C:
            decisions = held_out_decisions(corpus, everything, c, SELECTION_SEED)
            print(f"C={c:<4}", evaluate(corpus.labels[test], decisions[test]))
    elif args.compare:
        compare(corpus, args.c)
    else:
        model, metadata = train(corpus, args.c)
        save_model(model, metadata)
        print(json.dumps(metadata, indent=1))


if __name__ == "__main__":
    main()
