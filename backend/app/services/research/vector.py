"""Vector index for research retrieval.

What this is
------------
A deterministic 256-dimension hashed lexical vector, stored as an array in
`research_chunks.embedding`, scored with cosine similarity.

What this is **not**
--------------------
It is not a neural embedding. A real sentence-transformer would require either a
model download or an API key, and this build has neither. Rather than pretend,
the pipeline records the model name (`hash-256`) on every chunk, and the API
exposes it so a reviewer can see what produced a score.

Why bother with a vector at all, then? Because the retrieval *pipeline* is the
part that needs to be right — chunking, locators, fusion with lexical search,
citation assembly — and a deterministic vector lets that pipeline be built,
tested and replaced. Swapping `embed()` for a provider call later changes one
function and nothing else.

Hashed lexical vectors with IDF weighting work reasonably for term-overlap
retrieval over a few thousand short chunks. They fail on paraphrase, and that
failure is documented in docs/architecture/phase-4.md rather than hidden.
"""

from __future__ import annotations

import hashlib
import math
import re

DIMENSIONS = 256
MODEL_NAME = "hash-256"

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Function words and news boilerplate carry no retrieval signal and would
# dominate a hashed bag-of-words.
STOPWORDS = frozenset(
    """
    a an and are as at be been but by for from has have had he her his i in into is it its
    of on or our she that the their them there these they this to was were what when where
    which who will with would you your we us not no than then so if more most other such
    about above after again all also am any because before being below between both can
    did do does doing down during each few further here how just once only out over own same
    should some still too under until up very while why s t d ll m re ve y
    """.split()
)


def tokenise(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS and len(token) > 2]


def _hash(token: str) -> tuple[int, float]:
    """Map a token to a dimension and a sign.

    The sign comes from a second hash so collisions cancel probabilistically
    instead of always reinforcing each other.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    dimension = value % DIMENSIONS
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return dimension, sign


def embed(text: str, *, idf: dict[str, float] | None = None) -> list[float]:
    """Embed text into a unit-length 256-dimension vector.

    Sub-linear term frequency (1 + log tf) prevents one repeated word from
    dominating; IDF weighting, when supplied, down-weights terms that appear in
    every document.
    """
    vector = [0.0] * DIMENSIONS
    tokens = tokenise(text)
    if not tokens:
        return vector

    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    for token, count in counts.items():
        weight = 1.0 + math.log(count)
        if idf is not None:
            weight *= idf.get(token, 1.0)
        dimension, sign = _hash(token)
        vector[dimension] += sign * weight

    return normalise(vector)


def normalise(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    """Both vectors are unit length, so the dot product is the cosine."""
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def serialise(vector: list[float]) -> str:
    """Store as comma-separated rounded floats — compact and portable.

    A Postgres `vector` column would be better, but the platform must run on
    SQLite for the demo and CI, and a portable column keeps the two identical.
    """
    return ",".join(f"{value:.5f}" for value in vector)


def deserialise(raw: str) -> list[float]:
    if not raw:
        return []
    try:
        return [float(part) for part in raw.split(",")]
    except ValueError:
        return []


def build_idf(corpus: list[str]) -> dict[str, float]:
    """Inverse document frequency across the corpus currently being indexed."""
    document_count = max(len(corpus), 1)
    appearances: dict[str, int] = {}
    for text in corpus:
        for token in set(tokenise(text)):
            appearances[token] = appearances.get(token, 0) + 1
    return {
        token: math.log((document_count + 1) / (count + 1)) + 1.0
        for token, count in appearances.items()
    }
