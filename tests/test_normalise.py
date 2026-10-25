"""Table-driven tests for numerals, units and abbreviations (ported from narrator_prep.spoken_fixes)."""
from collections import Counter

import pytest

from fiatvox.narrate.normalise import cardinal, normalise, ordinal, regnal_names, roman

NUMERALS = [
    # world wars
    ("World War II ended.", "World War Two ended."),
    ("World War I began.", "World War One began."),
    ("After WWII the army shrank.", "After World War Two the army shrank."),
    ("In WW2 and WW1.", "In World War Two and World War One."),
    ("WWI veterans", "World War One veterans"),
    # known limit, kept from the old rules: after "World War" or "Great War" a lone I is always the numeral
    ("the Great War I mean", "the Great War One mean"),
    ("before the Great War I.", "before the Great War One."),
    # things followed by a numeral
    ("Phase I of the plan.", "Phase One of the plan."),
    ("See Chapter IV for details.", "See Chapter Four for details."),
    ("Part XII, the end", "Part Twelve, the end"),
    ("the Minuteman III missile", "the Minuteman Three missile"),
    ("Vatican II reforms", "Vatican Two reforms"),
    ("The Road to War I discussed", "The Road to War I discussed"),
    ("Appendix II.", "Appendix Two."),
    ("In Lecture III Boyd argues", "In Lecture Three Boyd argues"),
    ("Lectures I and II", "Lectures One and II"),
    # regnal names
    ("Napoleon III was emperor.", "Napoleon the Third was emperor."),
    ("Henry VIII married often.", "Henry the Eighth married often."),
    ("Louis XIV built Versailles.", "Louis the Fourteenth built Versailles."),
    ("Pope Pius XII spoke.", "Pope Pius the Twelfth spoke."),
    ("King Edward VII of England", "King Edward the Seventh of England"),
    ("He told Henry I was leaving.", "He told Henry I was leaving."),
    ("Henry I, king of England", "Henry the First, king of England"),
    ("Archbishop Makarios III spoke.", "Archbishop Makarios the Third spoke."),
    ("Queen Elizabeth II", "Queen Elizabeth the Second"),
    # suffixes after a full name
    ("John Smith III arrived.", "John Smith the Third arrived."),
    ("Thurston Howell III laughed.", "Thurston Howell the Third laughed."),
    # corps
    ("XVIII Airborne Corps attacked.", "Eighteenth Airborne Corps attacked."),
    ("I Corps held the line.", "I Corps held the line."),
    ("III Marine Expeditionary Force", "Third Marine Expeditionary Force"),
    ("VII Corps moved.", "Seventh Corps moved."),
    # things that must not change
    ("I think so.", "I think so."),
    ("The letter X marks it.", "The letter X marks it."),
    ("Model L was rare.", "Model L was rare."),
    ("Super Bowl L", "Super Bowl L"),
    ("Malcolm X spoke.", "Malcolm X spoke."),
    ("Chapter One", "Chapter One"),
]

UNITS = [
    ("a 500 lb bomb", "a 500 pounds bomb"),
    ("weighed 1 lb only", "weighed 1 pound only"),
    ("a 10-ft pole", "a 10-foot pole"),
    ("3 km away", "3 kilometers away"),
    ("1,200 mi of road", "1,200 miles of road"),
    ("2.5 kg of flour", "2.5 kilograms of flour"),
    ("at 30 mph, slowly", "at 30 miles per hour, slowly"),
    ("120mm guns", "120 millimeters guns"),
    ("a 2-hr wait", "a 2-hour wait"),
    ("twenty lbs of powder", "twenty pounds of powder"),
    ("5 min.", "5 minutes."),
    ("lbs.", "pounds."),
    ("the bulb lit", "the bulb lit"),
    ("a 3 km. walk", "a 3 kilometers walk"),
]

ABBREVIATIONS = [
    # initialisms
    ("The U.S. Army landed.", "The US Army landed."),
    ("He went to the U.S. The next day he left.", "He went to the US. The next day he left."),
    ("in the U.K., however", "in the UK, however"),
    ("He joined the U.S.", "He joined the US."),
    ("H.E. shells fell.", "H E shells fell."),
    ("the U.S.-Mexico border", "the US-Mexico border"),
    ("the U.S.S.R. collapsed", "the USSR collapsed"),
    ("A.D. 1066", "AD 1066"),
    # ranks and titles
    ("Lt. Col. Smith arrived.", "Lieutenant Colonel Smith arrived."),
    ("Gen. Patton attacked.", "General Patton attacked."),
    ("Dr. Jones and Mr. Brown", "Doctor Jones and Mister Brown"),
    ("Mrs. Brown and Ms. Green", "Missus Brown and Miz Green"),
    ("St. Louis is a city.", "Saint Louis is a city."),
    ("Ft. Bragg, North Carolina", "Fort Bragg, North Carolina"),
    ("Brig. Gen. Ward", "Brigadier General Ward"),
    ("Sgt. Major Cole", "Sergeant Major Cole"),
    ("on Main St. The next day", "on Main St. The next day"),
    ("the col. was tired", "the col. was tired"),
    # suffixes, numbers, etc.
    ("Martin Luther King, Jr. spoke.", "Martin Luther King, Junior spoke."),
    ("It was Davis Jr. The crowd cheered.", "It was Davis Junior. The crowd cheered."),
    ("John Smith Sr., the father", "John Smith Senior, the father"),
    ("No. 10 Downing Street", "Number 10 Downing Street"),
    ("Nos. 3 and 4", "Numbers 3 and 4"),
    ("guns, tanks, etc. were lost", "guns, tanks, et cetera were lost"),
    ("guns, tanks, etc. The war went on.", "guns, tanks, et cetera. The war went on."),
    ("guns, tanks, etc.", "guns, tanks, et cetera."),
    # Latin
    ("some, e.g. tanks", "some, for example, tanks"),
    ("some, i.e., tanks", "some, that is, tanks"),
    ("Roe v. Wade", "Roe versus Wade"),
    ("England vs. France", "England versus France"),
    ("born ca. 1820", "born circa 1820"),
    ("approx. ten miles", "approximately ten miles"),
    # middle initials
    ("James A. Baker said", "James A Baker said"),
    ("Harry S. Truman won.", "Harry S Truman won."),
    ("It was Plan A. The Army moved.", "It was Plan A. The Army moved."),
]


@pytest.mark.parametrize("before,after", NUMERALS)
def test_numerals(before, after):
    assert normalise(before) == after


@pytest.mark.parametrize("before,after", UNITS)
def test_units(before, after):
    assert normalise(before) == after


@pytest.mark.parametrize("before,after", ABBREVIATIONS)
def test_abbreviations(before, after):
    assert normalise(before) == after


def test_regnal_names_from_book_titles():
    book_text = "Archbishop Makarios III led Cyprus. Later Makarios III was exiled."
    names = regnal_names(book_text)
    assert names == {"Makarios"}
    assert normalise("Makarios III returned.", extra_regnal=names) == "Makarios the Third returned."
    # without the book's own evidence, a lone capitalised name plus numeral reads as a plain number
    assert normalise("Makarios III returned.") == "Makarios Three returned."


def test_log_records_changes():
    log = Counter()
    normalise("World War II and the U.S. Army, 500 lb.", log)
    assert log["War II -> War Two"] == 1
    assert log["U.S. -> US"] == 1


@pytest.mark.parametrize("s,v", [("I", 1), ("IV", 4), ("IX", 9), ("XIV", 14), ("XVIII", 18), ("XL", 40),
                                 ("XC", 90), ("XCIX", 99), ("IIII", 0), ("VX", 0), ("ABC", 0), ("", 0)])
def test_roman(s, v):
    assert roman(s) == v


@pytest.mark.parametrize("n,c,o", [(1, "one", "first"), (12, "twelve", "twelfth"), (20, "twenty", "twentieth"),
                                   (21, "twenty-one", "twenty-first"), (99, "ninety-nine", "ninety-ninth")])
def test_number_words(n, c, o):
    assert cardinal(n) == c and ordinal(n) == o


def test_idempotent():
    s = "World War II, Napoleon III, the U.S. Army, Lt. Col. Smith, 500 lb, XVIII Airborne Corps."
    once = normalise(s)
    assert normalise(once) == once
