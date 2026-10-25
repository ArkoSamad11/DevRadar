"""Score the title classifier against the hand-labeled sample.

usage: uv run python scripts/evaluate_classifier.py [tune|holdout|all] [--errors]
"""

import csv
import sys
from collections import Counter
from pathlib import Path

from devradar.ingestion.classify import categorize, is_internship

SAMPLE = Path(__file__).parent.parent / "tests" / "fixtures" / "classifier_sample.csv"


def load(split: str) -> list[dict]:
    with SAMPLE.open() as f:
        rows = list(csv.DictReader(f))
    return [row for row in rows if split == "all" or row["split"] == split]


def score(rows: list[dict]) -> tuple[dict[str, tuple[int, int, int]], list[str]]:
    counts: dict[str, Counter] = {}
    errors = []
    for row in rows:
        expected = set(filter(None, row["categories"].split(";")))
        predicted = categorize(row["title"])
        for slug in expected | predicted:
            c = counts.setdefault(slug, Counter())
            c[
                "tp"
                if slug in expected and slug in predicted
                else "fp"
                if slug in predicted
                else "fn"
            ] += 1
        if expected != predicted:
            errors.append(
                f"  {row['title']!r}: expected {sorted(expected)}, got {sorted(predicted)}"
            )
    return {slug: (c["tp"], c["fp"], c["fn"]) for slug, c in counts.items()}, errors


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.2f}" if denominator else "  - "


def main() -> None:
    split = (
        sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "tune"
    )
    rows = load(split)
    per_category, errors = score(rows)

    print(f"{split}: {len(rows)} titles")
    print(f"{'category':22} {'tp':>3} {'fp':>3} {'fn':>3}  precision  recall")
    totals = Counter()
    for slug, (tp, fp, fn) in sorted(per_category.items()):
        totals.update(tp=tp, fp=fp, fn=fn)
        print(
            f"{slug:22} {tp:3} {fp:3} {fn:3}  {ratio(tp, tp + fp):>9}  {ratio(tp, tp + fn):>6}"
        )
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    print(
        f"{'micro':22} {tp:3} {fp:3} {fn:3}  {ratio(tp, tp + fp):>9}  {ratio(tp, tp + fn):>6}"
    )

    flagged = [row for row in rows if is_internship(row["title"])]
    real = sum(row["is_internship"] == "yes" for row in rows)
    print(
        f"internship: {real}/{len(rows)} labeled real; title rule flags {len(flagged)}"
    )

    if "--errors" in sys.argv:
        print("errors:")
        print("\n".join(errors))


if __name__ == "__main__":
    main()
