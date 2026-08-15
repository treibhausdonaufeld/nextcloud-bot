"""Tests for German-aware search normalization (lemmas, compound splitting)."""

from app.db import fts_escape
from app.models.protocol import Protocol
from app.textnorm import (
    compound_parts,
    index_terms,
    lemmatize,
    plain_name,
    strip_markdown,
    strip_mentions,
    token_variants,
)


class TestPlainName:
    """Chat channel names come out of the wiki wrapped in markdown."""

    def test_markdown_link_keeps_the_text(self):
        assert (
            plain_name("[AG Struktur](https://chat.example.at/channel/AG-Struktur)")
            == "AG Struktur"
        )

    def test_bold_link_is_unwrapped(self):
        assert plain_name("**[AG Struktur](https://chat.example.at/x)**") == (
            "AG Struktur"
        )

    def test_emphasis_is_removed(self):
        assert plain_name("*Fragen an die AG*") == "Fragen an die AG"
        assert plain_name("`Termine`") == "Termine"

    def test_bare_link_leaves_nothing(self):
        assert plain_name("https://chat.example.at/channel/AG-Struktur") == ""

    def test_autolink_leaves_nothing(self):
        assert plain_name("<https://chat.example.at/channel/x>") == ""

    def test_whitespace_is_collapsed(self):
        assert plain_name("  AG   Struktur \n") == "AG Struktur"

    def test_list_bullet_is_dropped(self):
        assert plain_name("- AG Struktur") == "AG Struktur"

    def test_plain_name_is_unchanged(self):
        assert plain_name("Fragen an AG Struktur") == "Fragen an AG Struktur"

    def test_umlauts_survive(self):
        assert plain_name("**Ankündigungen**") == "Ankündigungen"

    def test_empty(self):
        assert plain_name("") == ""
        assert plain_name(None) == ""


class TestStripMarkdown:
    def test_nested_formatting_is_unwrapped(self):
        assert strip_markdown("**[Titel](https://x)**") == "Titel"

    def test_image_alt_text_is_kept(self):
        assert strip_markdown("![Logo](https://x/logo.png)") == "Logo"


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
        # "Bud"/"Get" are not known German words, so "Budget" stays whole
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


class TestStripMentions:
    def test_link_mention_becomes_plain_name(self):
        assert (
            strip_mentions(
                "@[Barbara Mayer](mention://user/Barbara.Mayer) "
                "ist Delegierte der AG Viertel"
            )
            == "Barbara Mayer ist Delegierte der AG Viertel"
        )

    def test_link_mention_without_at_prefix(self):
        assert (
            strip_mentions("[Barbara Mayer](mention://user/Barbara.Mayer)")
            == "Barbara Mayer"
        )

    def test_bare_mention_falls_back_to_username(self):
        assert strip_mentions("mention://user/alice ist dabei") == "alice ist dabei"

    def test_text_without_mentions_is_unchanged(self):
        assert strip_mentions("Just a normal title") == "Just a normal title"

    def test_empty(self):
        assert strip_mentions("") == ""
        assert strip_mentions(None) == ""


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
