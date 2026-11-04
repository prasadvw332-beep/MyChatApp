"""BM25 as sparse vectors, for hybrid search (HYBRID_SEARCH=true).

Dense vectors (embeddings) match meaning: "time off" finds "leave policy". They're weak on exact
tokens: a product code "XR-4471", an error "E1023", a rare surname. Keyword search (BM25) is the
opposite. Hybrid search runs both and fuses the rankings (fusion.py).

A sparse vector is {token index: weight} for the few tokens a text contains, out of a vocabulary
of millions. BM25 weighs a token by
    term frequency, saturated (the 10th "leave" adds less than the 2nd) and length-normalised
    x inverse document frequency (rare tokens count more)
The first part is computed here per chunk. The IDF part depends on the whole collection, so Qdrant
computes it at query time (Modifier.IDF on the sparse vector), which keeps it right as documents
come and go.

Token -> index uses a stable hash (crc32), not Python's hash() (randomised per process): the
backend and every worker must map "leave" to the same index.
"""

import re
import zlib
from collections import Counter

K1 = 1.2  # how quickly repeated terms stop adding weight
B = 0.75  # how strongly long chunks are penalised
AVG_TOKENS = 120  # roughly a chunk of 800 characters

STOPWORDS = set(
    "a an and are as at be but by for from has have he her his i if in into is it its me my no not of on "
    "or our she so than that the their them then there these they this to was we were what when where "
    "which who why will with you your".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def _index(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF


def _vector(weights: dict[int, float]) -> tuple[list[int], list[float]]:
    return list(weights), list(weights.values())


def document_vector(text: str) -> tuple[list[int], list[float]]:
    """(indices, values) for a chunk: the BM25 term-frequency part of each token's weight."""
    toks = tokenize(text)
    length_norm = 1 - B + B * len(toks) / AVG_TOKENS
    weights: dict[int, float] = {}
    for token, tf in Counter(toks).items():
        # two tokens may hash to the same index: rare enough to ignore, their weights add up
        weights[_index(token)] = weights.get(_index(token), 0.0) + tf * (K1 + 1) / (tf + K1 * length_norm)
    return _vector(weights)


def query_vector(text: str) -> tuple[list[int], list[float]]:
    """(indices, values) for a query: each distinct token once; Qdrant multiplies in the IDF."""
    return _vector({_index(t): 1.0 for t in set(tokenize(text))})
