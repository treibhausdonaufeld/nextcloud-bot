"""German-aware text normalization for the full-text search index.

FTS5's unicode61 tokenizer has no stemming and German is rich in inflection
("Gießkannen" vs. "Gießkanne", "gekauft" vs. "kaufen") and compounds
("Gartengeräte" never matches "Geräte"). To bridge that gap:

- documents get an extra `lemmas` FTS column containing the lemmatized form
  of every token plus the (lemmatized) parts of split compounds
- queries are expanded so each term also matches via its lemma and compound
  parts

Lemmatization uses simplemma (German + English). Compound splitting is
dictionary-based on simplemma's vocabulary: a long word is split when both
halves (allowing a German linking element like the Fugen-s) are known words.
Results are memoized.
"""

import re
from functools import lru_cache

import simplemma

TOKEN_RE = re.compile(r"\w[\w-]*")

LANGS = ("de", "en")

# Markdown inline/block formatting stripped when turning content into plain
# text for previews. Order matters: link/image alt text is kept, the other
# constructs keep their inner content.
_MD_FORMATTING_RE = re.compile(
    r"\[([^\]]+)\]\([^)]+\)"  # [text](url) -> text
    r"|~~(.+?)~~"  # ~~strikethrough~~ -> content
    r"|`([^`]+)`"  # `inline code` -> content
    r"|\*\*(.+?)\*\*"  # **bold** -> content
    r"|__(.+?)__"  # __bold__ -> content
    r"|\*(.+?)\*"  # *italic* -> content
    r"|_(.+?)_"  # _italic_ -> content
    r"|^#{1,6}\s*",  # heading markers -> remove
    re.MULTILINE,
)


def strip_markdown(text: str) -> str:
    """Remove markdown formatting characters from plain text for display."""
    if not text:
        return ""
    result = _MD_FORMATTING_RE.sub(
        lambda m: next(g for g in m.groups() if g is not None)
        if any(g is not None for g in m.groups())
        else "",
        text,
    )
    return result.strip()


# only try to split reasonably long, purely alphabetic words into parts that
# are proper words themselves (avoids junk like "Budget" -> "Bud"/"Get")
MIN_COMPOUND_LENGTH = 8
MIN_PART_LENGTH = 4

# German linking elements (Fugenelemente) tried between compound parts,
# e.g. "Arbeitszeit" -> "Arbeit" + s + "Zeit"
LINKING_ELEMENTS = ("s", "es", "n", "en", "er", "e")


@lru_cache(maxsize=100_000)
def lemmatize(token: str) -> str:
    """Lowercased lemma of a token (falls back to the token itself)."""
    try:
        return simplemma.lemmatize(token, lang=LANGS).lower()
    except Exception:
        return token.lower()


@lru_cache(maxsize=100_000)
def _is_word(word: str) -> bool:
    return simplemma.is_known(word, lang="de") or simplemma.is_known(
        word.capitalize(), lang="de"
    )


def _split_compound(token: str) -> tuple[str, str] | None:
    """Split a lowercased word into two known words, or None."""
    candidates = []
    for i in range(MIN_PART_LENGTH, len(token) - MIN_PART_LENGTH + 1):
        first, second = token[:i], token[i:]
        if not _is_word(second):
            continue
        if _is_word(first):
            candidates.append((len(second), first, second))
            continue
        # allow a linking element at the end of the first part
        for link in LINKING_ELEMENTS:
            stem = first[: -len(link)] if first.endswith(link) else None
            if stem and len(stem) >= MIN_PART_LENGTH and _is_word(stem):
                candidates.append((len(second), stem, second))
                break
    if not candidates:
        return None
    # prefer the split with the longest second part (the head of the compound)
    _, first, second = max(candidates)
    return first, second


@lru_cache(maxsize=100_000)
def compound_parts(token: str) -> tuple[str, ...]:
    """Parts of a German compound word, lowercased, incl. their lemmas."""
    if len(token) < MIN_COMPOUND_LENGTH or not token.isalpha():
        return ()
    split = _split_compound(token.lower())
    if split is None:
        return ()

    result: list[str] = []
    for part in split:
        if part not in result:
            result.append(part)
        # compound parts are usually nouns — lemmatize capitalized to avoid
        # verb readings (e.g. "garten" -> "garen")
        lemma = lemmatize(part.capitalize())
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
