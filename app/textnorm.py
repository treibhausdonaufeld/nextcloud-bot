"""German-aware text normalization for the full-text search index.

FTS5's unicode61 tokenizer has no stemming and German is rich in inflection
("Gießkannen" vs. "Gießkanne", "gekauft" vs. "kaufen") and compounds
("Gartengeräte" never matches "Geräte"). To bridge that gap:

- documents get an extra `lemmas` FTS column containing the lemmatized form
  of every token plus the (lemmatized) parts of split compounds
- queries are expanded so each term also matches via its lemma and compound
  parts

Lemmatization uses simplemma (German + English), compound splitting uses
compound-split (CharSplit). Both are pure Python; results are memoized.
"""

import re
from functools import lru_cache

import simplemma
from compound_split import char_split

TOKEN_RE = re.compile(r"\w[\w-]*")

LANGS = ("de", "en")

# only try to split reasonably long, purely alphabetic words, and only accept
# confident splits (e.g. "Budget" -> "Bud"/"Get" scores ~0.43 and is junk)
MIN_COMPOUND_LENGTH = 8
COMPOUND_SCORE_THRESHOLD = 0.6
MIN_PART_LENGTH = 3


@lru_cache(maxsize=100_000)
def lemmatize(token: str) -> str:
    """Lowercased lemma of a token (falls back to the token itself)."""
    try:
        return simplemma.lemmatize(token, lang=LANGS).lower()
    except Exception:
        return token.lower()


@lru_cache(maxsize=100_000)
def compound_parts(token: str) -> tuple[str, ...]:
    """Parts of a German compound word, lowercased, incl. their lemmas."""
    if len(token) < MIN_COMPOUND_LENGTH or not token.isalpha():
        return ()
    try:
        candidates = char_split.split_compound(token)
    except Exception:
        return ()
    if not candidates:
        return ()
    score, *parts = candidates[0]
    if score < COMPOUND_SCORE_THRESHOLD:
        return ()

    result: list[str] = []
    for part in parts:
        if len(part) < MIN_PART_LENGTH:
            continue
        lowered = part.lower()
        if lowered not in result:
            result.append(lowered)
        lemma = lemmatize(part)
        if lemma not in result:
            result.append(lemma)
    return tuple(result)


def token_variants(token: str) -> list[str]:
    """All lowercased search variants of a token (original first)."""
    variants = [token.lower()]
    lemma = lemmatize(token)
    if lemma not in variants:
        variants.append(lemma)
    for part in compound_parts(token):
        if part not in variants:
            variants.append(part)
    return variants


def index_terms(text: str) -> str:
    """Additional terms to index for a document (lemmas + compound parts).

    The original tokens are already indexed via the title/body columns, so
    only variants differing from the original token are emitted.
    """
    if not text:
        return ""

    terms: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        lowered = token.lower()
        for variant in token_variants(token):
            if variant != lowered and variant not in seen:
                seen.add(variant)
                terms.append(variant)
    return " ".join(terms)
