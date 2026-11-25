import json
import math
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

from devradar.roles.text import ROLES, tokenize

MODEL_PATH = Path(__file__).parent / "model.npz"
METADATA_PATH = Path(__file__).parent / "model.json"


@dataclass(frozen=True)
class RoleModel:
    vocabulary: dict[str, int]
    idf: np.ndarray
    weights: np.ndarray
    intercepts: np.ndarray
    slopes: np.ndarray
    offsets: np.ndarray

    def scores(self, text: str) -> dict[str, float]:
        columns, values = self.vectorize(text)
        decisions = self.weights[:, columns] @ values + self.intercepts
        probabilities = 1 / (1 + np.exp(-(self.slopes * decisions + self.offsets)))
        return dict(zip(ROLES, probabilities.tolist(), strict=True))

    def explain(self, text: str, role: str, limit: int = 5) -> list[tuple[str, float]]:
        columns, values = self.vectorize(text)
        contributions = self.weights[ROLES.index(role), columns] * values
        terms = list(self.vocabulary)
        top = np.argsort(-contributions)[:limit]
        return [
            (terms[columns[i]], float(contributions[i]))
            for i in top
            if contributions[i] > 0
        ]

    def vectorize(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        counts = Counter(t for t in tokenize(text) if t in self.vocabulary)
        columns = np.array([self.vocabulary[term] for term in counts], dtype=np.intp)
        frequencies = np.array([1 + math.log(count) for count in counts.values()])
        values = frequencies * self.idf[columns]
        norm = np.linalg.norm(values)
        return columns, values / norm if norm else values


def save_model(
    model: RoleModel, metadata: dict, directory: Path = MODEL_PATH.parent
) -> None:
    np.savez_compressed(
        directory / MODEL_PATH.name,
        terms=np.frombuffer("\n".join(model.vocabulary).encode(), dtype=np.uint8),
        idf=model.idf.astype(np.float32),
        weights=model.weights.astype(np.float32),
        intercepts=model.intercepts,
        slopes=model.slopes,
        offsets=model.offsets,
    )
    text = json.dumps(metadata, indent=1) + "\n"
    (directory / METADATA_PATH.name).write_text(text)


@cache
def load_model(path: Path = MODEL_PATH) -> RoleModel:
    with np.load(path) as data:
        return RoleModel(
            vocabulary={
                term: i
                for i, term in enumerate(data["terms"].tobytes().decode().split("\n"))
            },
            idf=data["idf"],
            weights=data["weights"],
            intercepts=data["intercepts"],
            slopes=data["slopes"],
            offsets=data["offsets"],
        )
