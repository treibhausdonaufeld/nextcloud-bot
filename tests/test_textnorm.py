"""Tests for German-aware search normalization (lemmas, compound splitting)."""

from app.db import fts_escape
from app.models.protocol import Protocol
from app.textnorm import compound_parts, index_terms, lemmatize, token_variants


class TestLemmatize:
    def test_german_inflections(self):
        assert lemmatize("Gießkannen") == "gießkanne"
        assert lemmatize("gekauft") == "kaufen"
        assert lemmatize("Mitglieder") == "mitglied"

    def test_english(self):
        assert lemmatize("bought") == "buy"


class TestCompoundParts:
    def test_splits_confident_compounds(self):
        parts = compound_parts("Gartengeräte")
        assert "garten" in parts
        assert "geräte" in parts or "gerät" in parts

    def test_rejects_low_confidence_splits(self):
        # "Budget" -> "Bud"/"Get" scores below the threshold
        assert compound_parts("Budget") == ()

    def test_skips_short_and_non_alpha_tokens(self):
        assert compound_parts("Haus") == ()
        assert compound_parts("2024-01-10") == ()


class TestTokenVariants:
    def test_variants_include_original_and_lemma(self):
        variants = token_variants("Gießkannen")
        assert variants[0] == "gießkannen"
        assert "gießkanne" in variants

    def test_compound_variants(self):
        variants = token_variants("Mitgliederversammlung")
        assert "mitglieder" in variants or "mitglied" in variants
        assert "versammlung" in variants


class TestIndexTerms:
    def test_emits_only_additional_variants(self):
        terms = index_terms("Wir haben Gießkannen gekauft").split()
        assert "gießkanne" in terms
        assert "kaufen" in terms
        # the original token is already indexed via title/body columns
        assert "gießkannen" not in terms

    def test_empty_text(self):
        assert index_terms("") == ""


class TestFtsEscape:
    def test_expands_terms_with_variants(self):
        match = fts_escape("Gießkannen")
        assert '"gießkannen"*' in match
        assert '"gießkanne"*' in match
        assert match.startswith("(") and " OR " in match

    def test_multiple_terms_are_anded(self):
        # terms whose lemma equals the token stay plain and are space-joined
        # (implicit AND in FTS5)
        assert fts_escape("Kompost kaufen") == '"kompost"* "kaufen"*'

    def test_strips_fts_syntax(self):
        assert '"' + "" not in fts_escape('"')
        assert fts_escape("") == ""


class TestHeadingBefore:
    CONTENT = (
        "# 2024-01-10 AG Garten\n"
        "Moderation: x\n"
        "## Budget für Frühjahr\n"
        "Diskussion über Anschaffungen.\n"
        "::: success\n**Beschluss: Gießkanne kaufen**\n:::\n"
        "## Nächstes Treffen\n"
        "::: success\n**Beschluss: Termin fixiert**\n:::\n"
    )

    def test_nearest_heading_is_found(self):
        position = self.CONTENT.index("::: success")
        assert Protocol.heading_before(self.CONTENT, position) == "Budget für Frühjahr"

        second = self.CONTENT.index("::: success", position + 1)
        assert Protocol.heading_before(self.CONTENT, second) == "Nächstes Treffen"

    def test_no_heading(self):
        assert Protocol.heading_before("no headings here\n::: success", 17) == ""
