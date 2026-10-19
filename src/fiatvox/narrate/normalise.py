"""Make text speakable: Roman numerals, units and abbreviations as words.

Ported from the old pipeline's narrator_prep.spoken_fixes, rule for rule and in the same order. Applied only to
the spoken copy of the text, never to chapter names shown in Books.

Why each rule exists:
  * Kokoro reads Roman numerals and unit abbreviations letter by letter ("Napoleon eye eye eye", "ell bee").
  * A period inside an abbreviation ("U.S. Army", "Lt. Col. Smith") reads as the end of a sentence: a pause in
    the middle of a phrase. Periods are removed except where the next word starts a new sentence.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
ORD_ONES = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
            "nineteenth"]
ORD_TENS = ["", "", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth", "eightieth",
            "ninetieth"]
NUMBER_WORDS = set(ONES + TENS + ORD_ONES + ORD_TENS + ["the", "and"]) - {""}


def cardinal(n: int) -> str:
    """1..99 as words: 21 -> twenty-one."""
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")


def ordinal(n: int) -> str:
    """1..99 as ordinal words: 21 -> twenty-first."""
    if n < 20:
        return ORD_ONES[n]
    return ORD_TENS[n // 10] if n % 10 == 0 else TENS[n // 10] + "-" + ORD_ONES[n % 10]


ROMAN_OK = re.compile(r"^(?=[IVXL])(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman(s: str) -> int:
    """Value of a Roman numeral from 1 to 99, or 0 if s is not one."""
    if not ROMAN_OK.match(s):
        return 0
    total = 0
    for i, ch in enumerate(s):
        v = ROMAN_VAL[ch]
        total += -v if i + 1 < len(s) and ROMAN_VAL[s[i + 1]] > v else v
    return total


# Words after which a numeral is a plain number: "World War II" -> "World War Two", "Phase I" -> "Phase One".
THING = ("War|Wars|Chapter|Part|Section|Book|Volume|Phase|Title|Article|Act|Scene|Appendix|Annex|Class|Type|Mark|"
         "Chapters|Parts|Sections|Books|Volumes|Phases|Titles|Articles|Acts|Appendices|Annexes|Classes|Types|Marks|"
         "Stage|Level|Tier|Category|Figure|Table|Schedule|Protocol|Group|Block|Bowl|Grade|Model|Series|Plan|Star|"
         "Operation|Round|Step|Echelon|Region|Zone|Area|Task|Case|Division|Convention|Amendment|"
         "Vatican|SALT|START|Apollo|Gemini|Saturn|Titan|Atlas|Minuteman|Trident|Pershing|Hellfire|Panzer|"
         "Tiger|Predator|Reaper|Tomahawk|Patriot|Javelin|Phantom|Thunderbolt|Globemaster|Raptor|Lightning|"
         "Hornet|Harrier|Nimitz|Abrams|Bradley|Stryker|Typhoon|Tornado|Rafale|Mirage|Spitfire|Sputnik|Polaris")
# Names after which a numeral is regnal: "Napoleon III" -> "Napoleon the Third".
REGNAL = ("Henry|Charles|Louis|George|Edward|William|James|Richard|Elizabeth|Napoleon|Frederick|Friedrich|Philip|"
          "Peter|Catherine|Nicholas|Alexander|Leo|Pius|Paul|John|Benedict|Gregory|Clement|Innocent|Urban|Francis|"
          "Ferdinand|Leopold|Wilhelm|Mary|Constantine|Otto|Ivan|Gustav|Gustavus|Christian|Carl|Karl|Alfonso|Pedro|"
          "Emmanuel|Darius|Xerxes|Ptolemy|Ramses|Selim|Suleiman|Mehmed|Murad|Abdul|Hamid|Victor|Franz|Joseph|"
          "Maximilian|Rudolf|Albert|Baldwin|Stephen|Harold|Robert|David|Mohammed|Muhammad|Hassan|Hussein|"
          "Abdullah|Faisal|Fuad|Farouk|Haakon|Olav|Harald|Juan|Felipe|Carlos|Manuel|Michael|Basil|Justinian|"
          "Theodosius|Antiochus|Seleucus|Mithridates|Philippe|Henri|Umberto|Boris|Simeon|Paschal|Sixtus|Julius|"
          "Adrian|Hadrian|Boniface|Celestine|Martin|Eugene|Honorius|Callixtus|Alexios|Andronikos|Leopoldo|Shapur|"
          "Khosrow|Artaxerxes|Cyrus|Amenhotep|Thutmose|Seti|Kim|Rama|Chulalongkorn|Mongkut|Kamehameha|Tupou")
NUM = r"[IVXL]+"
RE_THING = re.compile(r"\b(" + THING + r")\s+(" + NUM + r")\b(?!\s*[.]\s*[A-Z]\.)")
RE_REGNAL = re.compile(r"\b(" + REGNAL + r")\s+(" + NUM + r")\b")
RE_TITLED = re.compile(r"\b((?:King|Queen|Pope|Emperor|Empress|Tsar|Czar|Kaiser|Sultan|Shah|Prince|Princess|Duke|"
                       r"Count|Saint|St\.|Patriarch|Pharaoh|Caliph|Archbishop|Bishop|Cardinal|Ethnarch|Sheikh|Emir|"
                       r"Amir|Lord|Baron|Earl|Archduke|Margrave|Elector)\s+(?:[A-Z][a-z]+\s+){1,2})(" + NUM + r")\b")
# "John Smith III": two or more capitalised words, none of them a THING word, then a numeral of II or more.
RE_SUFFIX = re.compile(r"\b((?:(?:[A-Z][a-z]+|[A-Z]\.),?\s+){2,4})(" + NUM + r")\b")
# "the Minuteman III": one capitalised word then a numeral of II or more -> plain number.
RE_SINGLE = re.compile(r"(?<![A-Za-z] )\b([A-Z][a-z]{2,})\s+(" + NUM + r")\b")
# "XVIII Airborne Corps" -> "Eighteenth Airborne Corps". "I Corps" is left alone (it is said "Eye Corps").
RE_CORPS = re.compile(r"\b(" + NUM + r")(\s+(?:[A-Z][a-z]+\s+){0,2}(?:Corps|MEF|MAF|Marine Expeditionary Force|"
                      r"Fighter Command|Bomber Command|Tactical Air Command|Panzer Corps))\b")
# After a lone "I", these show it is a numeral rather than the pronoun.
I_FOLLOW = re.compile(r"\s*(?:[,.;:)’'!?]|$|\s(?:of|and|in)\b)")

UNITS = {  # abbreviation -> (singular, plural); applied only after a number
    "lb": ("pound", "pounds"), "lbs": ("pound", "pounds"), "oz": ("ounce", "ounces"), "ft": ("foot", "feet"),
    "yd": ("yard", "yards"), "yds": ("yard", "yards"), "mi": ("mile", "miles"), "km": ("kilometer", "kilometers"),
    "kg": ("kilogram", "kilograms"), "mm": ("millimeter", "millimeters"), "cm": ("centimeter", "centimeters"),
    "mph": ("mile per hour", "miles per hour"), "kph": ("kilometer per hour", "kilometers per hour"),
    "kts": ("knot", "knots"), "kt": ("knot", "knots"), "hr": ("hour", "hours"), "hrs": ("hour", "hours"),
    "min": ("minute", "minutes"), "mins": ("minute", "minutes"), "sec": ("second", "seconds"),
    "secs": ("second", "seconds"), "gal": ("gallon", "gallons"), "sq": ("square", "square"),
}
UNIT_ALT = "|".join(sorted(UNITS, key=len, reverse=True))
RE_UNIT_HYPHEN = re.compile(r"(\d)-(" + UNIT_ALT + r")\b\.?(?=[\s\-,;:)]|$)")
RE_UNIT = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s?(" + UNIT_ALT + r")\b(\.(?=\s+[a-z(]|[,;:)]))?")
RE_LB_ALONE = re.compile(r"(?<![A-Za-z0-9.-])(lbs?)\b(\.(?=\s+[a-z(]|[,;:)]))?")

# "WWII" is read "double-you double-you eye eye".
RE_WW = re.compile(r"\bWW\s?(II|I|2|1)\b")

# A period after an abbreviation is kept only where the next word looks like the start of a new sentence.
SENTENCE_START = (r"(?:The|This|That|These|Those|There|Then|Thus|It|Its|In|On|At|As|By|For|From|With|Without|He|She|"
                  r"They|We|I|You|His|Her|Their|Our|My|But|And|Or|Yet|So|A|An|If|When|While|Where|After|Before|"
                  r"Since|Although|Though|Because|Such|Some|Most|Many|Much|Each|Every|One|Two|Three|No|Not|Even|"
                  r"Still|Instead|However|Indeed|Today|Now|What|Why|How|Who|Despite|During|Under|Over|Into|To|"
                  r"Unlike|Like|Both|All|Other|Another|Only|Yes|Here)\b")
RE_SENTENCE_START = re.compile(r"\s+" + SENTENCE_START)
RE_INITIALISM = re.compile(r"\b((?:[A-Z]\.){2,})(?=(\s+\S|[-–—,;:)’'\"”]|$))")
RE_LATIN = [(re.compile(r"\be\.g\.,?"), "for example,"), (re.compile(r"\bi\.e\.,?"), "that is,"),
            (re.compile(r"\bvs?\.(?=\s)"), "versus"), (re.compile(r"\bca\.(?=\s+\d)"), "circa"),
            (re.compile(r"\bapprox\.(?=\s)"), "approximately")]
TITLES = {  # abbreviation -> spoken form; expanded only before a capitalised word (a name or another title)
    "Lt": "Lieutenant", "Lieut": "Lieutenant", "Col": "Colonel", "Gen": "General", "Brig": "Brigadier",
    "Capt": "Captain", "Cpt": "Captain", "Maj": "Major", "Sgt": "Sergeant", "SSgt": "Staff Sergeant",
    "Cpl": "Corporal", "Pvt": "Private", "Adm": "Admiral", "Cmdr": "Commander", "Cdr": "Commander",
    "Lt.Col": "Lieutenant Colonel", "Lt.Gen": "Lieutenant General", "Maj.Gen": "Major General",
    "Brig.Gen": "Brigadier General", "Gov": "Governor", "Sen": "Senator", "Rep": "Representative",
    "Pres": "President", "Prof": "Professor", "Dr": "Doctor", "Mr": "Mister", "Mrs": "Missus", "Ms": "Miz",
    "St": "Saint", "Ft": "Fort", "Mt": "Mount", "Rev": "Reverend", "Hon": "Honorable", "Sec": "Secretary",
}
RE_TITLE_ABBR = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(TITLES, key=len, reverse=True))
                           + r")\.(?=\s*[A-Z])")
RE_SUFFIX_ABBR = re.compile(r"(?<=[A-Za-z])(,?\s+)(Jr|Sr)\.(?=(\s+\S|[,;:)’'\"”]|$))")
RE_NUMBER_ABBR = re.compile(r"\b(No|Nos)\.(?=\s*\d)")
RE_ETC = re.compile(r"\betc\.(?=(\s+\S|[,;:)’'\"”]|$))")
# A middle initial: "James A. Baker" -> "James A Baker" (the letter is still read as a letter).
RE_MIDDLE_INITIAL = re.compile(r"(\b[A-Z][a-z]+ )([A-Z])\.(?= [A-Z][a-z])")

# Initialisms that would spell an ordinary word once their periods are removed ("H.E." -> "HE", read "he").
COMMON_WORDS = set("""he is as at it in on or to so no an am be by do go if me my of oh ok ox up we ah aw eh ha hi
ho lo ma pa yo the and for her him his its not was who may can all any are one our out see she two use way day ago
act age aid aim air arm art ask bad bag bar bay bed bet big bit box boy bus but buy car cat cow cry cup cut dad dog
dry due ear eat egg end era eve eye fan far fat fee few fig fit fix fly fog for fox fun fur gas get god got gun guy
had ham has hat hen hey hid hip hit hog hop hot how hug ice ill ink jam jar jaw jet job jog joy key kid kin kit lab
lad lap law lay led leg let lid lie lip lit log lot low mad man map mat men met mix mob mom mop mud mug nap net new
nod nor now nun nut oak oar odd off oil old opt our owl own pad pan pat paw pay pea pen per pet pie pig pin pit pop
pot pro pub put rag ram ran rat raw ray red rib rid rip rob rod rot row rub rug run sad sag sat saw say sea set sew
sir sit six ski sky sly son spy sub sum sun tab tag tan tap tar tax tea ten tie tin tip toe ton too top tow toy try
tub tug van vat vet vow war was wax web wed wet who why wig win wit woe won wow yes yet you zip zoo also area army
away back bank base bear been best bill body bomb book born both call came camp care case cash city club cold come
cost crew dark data date dead deal dear deep does done door down draw drop drug duty each east easy else even ever
face fact fall farm fast fear feel feet fell felt file fill film find fine fire firm fish five flat flow food foot
form four free from full fund gain game gave girl give goal goes gold gone good grew grow half hall hand hard harm
have head hear heat held hell help here hero high hill hire hold hole home hope host hour huge hung hunt idea into
iron item join jump just keep kept kill kind king knew know lack lady laid lake land last late lead left less life
lift like line link list live load loan lock long look lord lose loss lost love luck made mail main make male many
mark mass meal mean meat meet mile mind mine miss mode more most move much must name navy near neck need news next
nice nine none norm nose note once only open over pace pack page paid pain pair park part pass past path peak pick
plan play plot plus poll pool poor port post pull pure push race rain rank rate read real rely rest rich ride ring
rise risk road rock role roll room root rose rule rush safe said sake sale salt same sand save seat seek seem seen
self sell send sent ship shop shot show shut sick side sign site size skin slip slow snow soft soil sold sole some
song soon sort soul spot star stay step stop such suit sure take tale talk tall tank task team tell tend term test
text than that them then they thin this thus till time tiny told tone took tool tour town tree trip true tune turn
twin type unit upon used user vast very view vote wage wait wake walk wall want warm wash wave ways weak wear week
well went were west what when whom wide wife wild will wind wine wing wire wise wish with wood word wore work yard
yeah year your zero zone""".split())


def keep_period(following: str) -> bool:
    """Should an abbreviation's period stay because it also ends the sentence?"""
    if not (following or "").strip():
        return True  # end of the paragraph
    return bool(RE_SENTENCE_START.match(following))


def regnal_names(text: str) -> set[str]:
    """Names this book itself uses with a title ("Archbishop Makarios III"): treated as regnal throughout."""
    return {m.group(1).split()[-1] for m in RE_TITLED.finditer(text) if roman(m.group(2))}


def normalise(text: str, log: Counter | None = None, extra_regnal: Iterable[str] = ()) -> str:
    """Rewrite Roman numerals, units and abbreviations as words. `log` counts 'before -> after'."""
    log = log if log is not None else Counter()
    extra_regnal = set(extra_regnal)

    def rec(m: re.Match, new: str) -> str:
        if new != m.group(0):
            log[f"{m.group(0)} -> {new}"] += 1
        return new

    def thing(m):
        v = roman(m.group(2))
        if not v or (len(m.group(2)) == 1 and m.group(2) in "L"):
            return m.group(0)
        if (m.group(2) == "I" and not I_FOLLOW.match(m.string[m.end():])
                and not (m.group(1) == "War" and m.string[:m.start()].endswith(("World ", "Great ")))):
            return m.group(0)  # "The Road to War I discussed": the pronoun, not a numeral
        return rec(m, f"{m.group(1)} {cardinal(v).title()}")

    def regnal(m):
        v = roman(m.group(2))
        if not v or m.group(2) == "L":
            return m.group(0)
        if m.group(2) == "I" and not I_FOLLOW.match(m.string[m.end():]):
            return m.group(0)  # "... told Henry I was leaving"
        return rec(m, f"{m.group(1).rstrip()} the {ordinal(v).title()}")

    def suffix(m):
        v = roman(m.group(2))
        words = m.group(1).split()
        if (v < 2 or m.group(2) in ("L", "V", "X") or any(re.fullmatch(THING, w.strip(".,")) for w in words)
                or any(w.strip(".,").lower() in NUMBER_WORDS for w in words)):
            return m.group(0)
        return rec(m, f"{m.group(1).rstrip().rstrip(',')} the {ordinal(v).title()}")

    def single(m):
        v = roman(m.group(2))
        if m.group(1).lower() in NUMBER_WORDS:
            return m.group(0)
        if v >= 2 and m.group(1) in extra_regnal:
            return rec(m, f"{m.group(1)} the {ordinal(v).title()}")
        if v < 2 or m.group(2) in ("L", "V", "X", "XX", "XL", "LI", "LV", "LX", "XXX"):
            return m.group(0)
        return rec(m, f"{m.group(1)} {cardinal(v).title()}")

    def corps(m):
        v = roman(m.group(1))
        if v < 2 or m.group(1) == "L":
            return m.group(0)
        return rec(m, f"{ordinal(v).title()}{m.group(2)}")

    def unit(m):
        one, many = UNITS[m.group(2)]
        return rec(m, f"{m.group(1)} {one if m.group(1) in ('1', '1.0') else many}")

    def initialism(m):
        letters = m.group(1).replace(".", "")
        if letters.lower() in COMMON_WORDS:
            letters = " ".join(letters)  # "H.E." as "HE" would be read "he"; "H E" is read letter by letter
        keep = m.group(2)[:1].isspace() and keep_period(m.string[m.end():]) or m.group(2) == ""
        return rec(m, letters + ("." if keep else ""))

    def title_abbr(m):
        if keep_period(m.string[m.end():]):
            return m.group(0)  # "on Main St. The next day": the abbreviation ends a sentence
        return rec(m, TITLES[m.group(1)])

    def suffix_abbr(m):
        word = "Junior" if m.group(2) == "Jr" else "Senior"
        keep = keep_period(m.string[m.end():]) if m.group(3)[:1].isspace() or m.group(3) == "" else False
        return rec(m, f"{m.group(1)}{word}{'.' if keep else ''}")

    def etc(m):
        keep = keep_period(m.string[m.end():]) if m.group(1)[:1].isspace() or m.group(1) == "" else False
        return rec(m, "et cetera" + ("." if keep else ""))

    def middle_initial(m):
        if keep_period(m.string[m.end():]):
            return m.group(0)
        return rec(m, m.group(1) + m.group(2))

    text = RE_WW.sub(lambda m: rec(m, "World War " + ("Two" if m.group(1) in ("II", "2") else "One")), text)
    text = RE_CORPS.sub(corps, text)
    text = RE_THING.sub(thing, text)
    text = RE_TITLED.sub(regnal, text)
    text = RE_REGNAL.sub(regnal, text)
    text = RE_SUFFIX.sub(suffix, text)
    text = RE_SINGLE.sub(single, text)
    text = RE_UNIT_HYPHEN.sub(lambda m: rec(m, f"{m.group(1)}-{UNITS[m.group(2)][0]}"), text)
    text = RE_UNIT.sub(unit, text)
    text = RE_LB_ALONE.sub(lambda m: rec(m, "pounds" if m.group(1) == "lbs" else "pound"), text)
    text = RE_TITLE_ABBR.sub(title_abbr, text)
    text = RE_SUFFIX_ABBR.sub(suffix_abbr, text)
    text = RE_NUMBER_ABBR.sub(lambda m: rec(m, "Number" if m.group(1) == "No" else "Numbers"), text)
    text = RE_ETC.sub(etc, text)
    for pat, rep in RE_LATIN:
        text = pat.sub(lambda m, rep=rep: rec(m, rep), text)
    text = RE_INITIALISM.sub(initialism, text)
    text = RE_MIDDLE_INITIAL.sub(middle_initial, text)
    return text
