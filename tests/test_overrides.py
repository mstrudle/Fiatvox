"""Every shipped phoneme override must reach misaki as one token: the phonemes come through, the markup does not."""
import pytest

from fiatvox import paths
from fiatvox.narrate import chunk, pronounce
from fiatvox.narrate.g2p import markup_leaks, phonemes, unknown_symbols

SHIPPED = [(accent, word, ps)
           for accent, kind in pronounce.ACCENTS.items()
           for word, ps in pronounce.read_tsv(paths.shipped_pronunciation_dir() / f"{kind}.tsv")]


def test_shipped_lists_have_the_tested_values():
    brit = dict((w, p) for a, w, p in SHIPPED if a == "b")
    amer = dict((w, p) for a, w, p in SHIPPED if a == "a")
    assert brit == {"combatant": "/kəmbˈatənt/", "combatants": "/kəmbˈatənts/",
                    "noncombatant": "/nˌɒnkəmbˈatənt/", "noncombatants": "/nˌɒnkəmbˈatənts/",
                    "quasi": "/kwˈɑːzi/", "lieutenant": "/luːtˈɛnənt/", "lieutenants": "/luːtˈɛnənts/"}
    assert amer == {"quasi": "/kwˈɑzi/"}


@pytest.mark.slow
@pytest.mark.parametrize("accent,word,ps", SHIPPED, ids=[f"{a}-{w}" for a, w, _ in SHIPPED])
def test_override_survives_g2p(accent, word, ps):
    lex = {word.lower(): ps.strip("/")}
    for form in (word, word.capitalize()):
        sentence = pronounce.apply_overrides(f"The {form} was there, quasi-officially.", lex)
        assert f"[{form}](/" in sentence
        assert not chunk.check_unit(sentence)
        out = phonemes(sentence, accent)
        assert markup_leaks(sentence, accent) is None, out
        assert ps.strip("/") in out


@pytest.mark.slow
@pytest.mark.parametrize("accent,word,ps", SHIPPED, ids=[f"{a}-{w}" for a, w, _ in SHIPPED])
def test_override_symbols_known_to_kokoro(accent, word, ps):
    assert not unknown_symbols(ps.strip("/"))


@pytest.mark.slow
def test_whole_list_in_one_paragraph():
    """All overrides for an accent in one long paragraph, as a book would have them."""
    for accent in pronounce.ACCENTS:
        lex = pronounce.load_phonemes(accent)
        para = " ".join(f"Here the {w} stood, and another {w} followed." for w in lex) * 3
        spoken = pronounce.apply_overrides(para, lex)
        assert markup_leaks(spoken, accent) is None


def test_overrides_never_nest_or_split():
    lex = {"quasi": "kwˈɑzi"}
    once = pronounce.apply_overrides("quasi Quasi QUASI quasimodo", lex)
    assert once == "[quasi](/kwˈɑzi/) [Quasi](/kwˈɑzi/) [QUASI](/kwˈɑzi/) quasimodo"
    assert pronounce.apply_overrides(once, lex) == once
    with pytest.raises(ValueError):
        pronounce.override("two words", "x")


@pytest.mark.slow
def test_helper_checks_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("FIATVOX_DATA", str(tmp_path))
    from fiatvox.narrate import helper
    good = helper.check("quasi", "kwˈɑːzi", "b")
    assert good.ok and "kwˈɑːzi" in good.phonemes_out
    assert not helper.check("quasi", "kw ɑzi", "b").ok
    assert not helper.check("quasi", "kw§zi", "b").ok
    assert not helper.check("two words", "kwˈɑzi", "a").ok
    helper.save("Clausewitz", "klˈWzəvɪts", "b", "klow-zuh-vits")
    assert pronounce.load_phonemes("b")["clausewitz"] == "klˈWzəvɪts"
    assert "combatant" in pronounce.load_phonemes("b")   # shipped entries still there
