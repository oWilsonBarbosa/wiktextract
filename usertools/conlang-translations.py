#!/usr/bin/env python3
#
# Gather and analyze a concept's translations across many languages,
# as raw material for constructed-language (conlang) word building.
#
# It reads the translations that the English Wiktionary lists for a word
# (as extracted by Wiktextract / available from https://kaikki.org) and can:
#   * list them grouped by meaning (sense)
#   * export them to CSV (for Excel / Google Sheets)
#   * report sound/syllable patterns across the translations (--stats)
#   * filter to words that fit a target sound inventory (--inventory)
#
# You do not need to be a programmer to use it. Two ways to give it data:
#
#   1. From a local file you downloaded from kaikki.org:
#        python3 conlang-translations.py --file thunder.jsonl
#
#   2. By word, downloading on the fly (needs internet):
#        python3 conlang-translations.py thunder
#
# Examples:
#   python3 conlang-translations.py --file thunder.jsonl
#   python3 conlang-translations.py --file thunder.jsonl --sense lightning
#   python3 conlang-translations.py --file thunder.jsonl --csv out.csv
#   python3 conlang-translations.py --file thunder.jsonl --stats
#   python3 conlang-translations.py --file thunder.jsonl --inventory "p t k m n s r a i u"
#   python3 conlang-translations.py --file thunder.jsonl --generate 20 --seed 1
#   python3 conlang-translations.py --file thunder.jsonl --file rain.jsonl --compare
#   python3 conlang-translations.py --file ukkonen.jsonl --file grom.jsonl --ipa --stats
#
# NOTE ON "SOUNDS": by default the sound analysis uses each word's
# romanization (or the word itself for Latin-script languages), with
# accent/diacritic marks stripped -- an approximation, good enough for
# spotting cross-linguistic tendencies, but not a true phonemic transcription.
#
# For REAL phonemes, pass --ipa: it reads the IPA in each entry's `sounds`
# field and segments it into proper phonemes (affricates like t͡ʃ, length ː,
# etc. are kept whole). Translation lists almost never carry IPA, so --ipa is
# meant for word ENTRY files: download each language's own entry (e.g. the
# Finnish page for "ukkonen") and feed them with --file.

import sys
import csv as csv_module
import json
import argparse
import unicodedata
import urllib.request
import urllib.error
from collections import Counter


# --------------------------------------------------------------------------
# Loading data
# --------------------------------------------------------------------------

def kaikki_url(word):
    """Build the kaikki.org per-word data URL for an English entry."""
    w = word.strip().replace(" ", "_")
    return (f"https://kaikki.org/dictionary/English/meaning/"
            f"{w[0].lower()}/{w[:2].lower()}/{w}.jsonl")


def _records_label(records, fallback):
    """A human label for a concept: the English word the data is about."""
    for rec in records:
        if rec.get("word"):
            return rec["word"]
    return fallback


def download_records(word):
    url = kaikki_url(word)
    req = urllib.request.Request(url, headers={"User-Agent": "conlang-helper"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"No data found for '{word}' at {url}")
        sys.exit(f"Download failed ({e.code}): {url}")
    except Exception as e:
        sys.exit(f"Could not reach kaikki.org: {e}\n"
                 "Tip: download the .jsonl by hand and use --file instead.")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def load_sources(args):
    """Return a list of (concept_label, records) pairs.

    Accepts any number of --file inputs and/or a single downloaded word.
    """
    sources = []
    for path in args.file or []:
        with open(path, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        sources.append((_records_label(recs, path), recs))
    if args.word:
        recs = download_records(args.word)
        sources.append((_records_label(recs, args.word), recs))
    if not sources:
        sys.exit("Give a word, or one or more --file inputs. See --help.")
    return sources


def collect_translations(records, sense_filter=None):
    """Flatten translations into simple dict rows, optionally filtered by sense."""
    rows = []
    for rec in records:
        for tr in rec.get("translations", []):
            word = tr.get("word")
            if not word:
                continue  # note-only entry, no actual word
            sense = tr.get("sense", "")
            if sense_filter and sense_filter.lower() not in sense.lower():
                continue
            rows.append({
                "sense": sense,
                "lang": tr.get("lang", tr.get("code", "?")),
                "word": word,
                "roman": tr.get("roman", ""),
                "ipa": tr.get("ipa", ""),
                "tags": ", ".join(tr.get("tags", [])),
            })
    return rows


# An "analysis item" is a dict {lang, label, form, tags}, where `form` is the
# raw string we measure sounds on. Two ways to build them:

def rows_to_items(rows):
    """Items from translations: form = romanization (approximate sounds)."""
    return [{"lang": r["lang"], "label": r["word"],
             "form": sound_form(r), "tags": r["tags"]} for r in rows]


def collect_ipa(records, rows):
    """Items from REAL IPA in each entry's `sounds` field.

    Translation lists almost never carry IPA, so genuine phonemic analysis
    needs each word's own entry (e.g. download the Finnish entry for
    'ukkonen'). Any IPA that does appear on a translation is included too.
    """
    items = []
    for rec in records:
        lang, word = rec.get("lang", "?"), rec.get("word", "?")
        for s in rec.get("sounds", []):
            ipa = s.get("ipa")
            if ipa:
                items.append({"lang": lang, "label": word, "form": ipa,
                              "tags": ", ".join(s.get("tags", []))})
    for r in rows:
        if r.get("ipa"):
            items.append({"lang": r["lang"], "label": r["word"],
                          "form": r["ipa"], "tags": r["tags"]})
    return items


# --------------------------------------------------------------------------
# Sound handling: works on "units" -- either single Latin letters (from
# romanization, approximate) or real IPA phonemes (from the `sounds` field).
# --------------------------------------------------------------------------

LETTER_VOWELS = set("aeiou")
# IPA vowel symbols (base characters of the IPA vowel chart).
IPA_VOWELS = set("iyɨʉɯuɪʏʊeøɘɵɤoəɛœɜɞʌɔæɐaɶɑɒ")

# IPA marks to drop entirely (stress, syllable breaks, linking, delimiters).
_IPA_DROP = set("ˈˌ.‿|‖()[]/ ‍")
# Spacing modifiers that attach to the preceding phoneme (length, aspiration,
# palatalization, labialization, etc.).
_IPA_ATTACH = set("ːˑʰʷʲˠˤⁿˡʼˀˁ˞")
_TIE_BARS = ("͡", "͜")  # ◌͡ / ◌͜  join two symbols into one phoneme


def sound_form(row):
    """Best available 'how it sounds' string: romanization, else the word
    itself when it is already in Latin letters."""
    if row["roman"]:
        return row["roman"]
    return row["word"]


def normalize(text):
    """Lowercase, strip diacritics/accents, drop anything but letters."""
    text = text.lower()
    for sep in ("/", ",", ";"):
        if sep in text:
            text = text.split(sep)[0]
    text = unicodedata.normalize("NFD", text)
    out = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if ch.isalpha():
            out.append(ch)
    return "".join(out)


def tokenize_ipa(text):
    """Split an IPA string into phoneme-ish units.

    Handles tie-bar affricates (t͡ʃ), length (ː), and attaching diacritics /
    modifiers, while discarding stress and syllable marks. Approximate, but
    far closer to real phonemes than treating the string character by char.
    """
    chars = [c for c in text.strip() if c not in _IPA_DROP]
    units, i, n = [], 0, len(chars)
    while i < n:
        unit = chars[i]
        i += 1
        while i < n:
            d = chars[i]
            if d in _TIE_BARS:           # affricate / double articulation
                unit += d
                i += 1
                if i < n:                # pull in the second symbol
                    unit += chars[i]
                    i += 1
            elif unicodedata.combining(d) or d in _IPA_ATTACH:
                unit += d
                i += 1
            else:
                break
        units.append(unit)
    return units


def to_units(text, ipa=False):
    """Turn a form into a list of units: phonemes (IPA) or letters."""
    return tokenize_ipa(text) if ipa else list(normalize(text))


def is_vowel(unit, ipa=False):
    base = unicodedata.normalize("NFD", unit)
    base = base[0] if base else ""
    return base in (IPA_VOWELS if ipa else LETTER_VOWELS)


def cv_skeleton(units, ipa=False):
    """Map units to a C/V skeleton, e.g. ['g','r','o','m'] -> 'CCVC'."""
    return "".join("V" if is_vowel(u, ipa) else "C" for u in units)


def syllable_split(units, ipa=False):
    """Rough syllable split: each vowel group is a nucleus; trailing
    consonants attach to the preceding nucleus. Good enough for tallying."""
    sylls, cur, seen_vowel = [], [], False
    for u in units:
        v = is_vowel(u, ipa)
        prev_v = cur and is_vowel(cur[-1], ipa)
        if v and seen_vowel and not prev_v:
            sylls.append(cur)
            cur, seen_vowel = [u], True
        else:
            if v:
                seen_vowel = True
            cur.append(u)
    if cur:
        sylls.append(cur)
    return ["".join(s) for s in sylls if s]


def onset(units, ipa=False):
    """Leading consonant cluster as a string (may be empty)."""
    i = 0
    while i < len(units) and not is_vowel(units[i], ipa):
        i += 1
    return "".join(units[:i])


def coda(units, ipa=False):
    """Trailing consonant cluster as a string (may be empty)."""
    i = len(units)
    while i > 0 and not is_vowel(units[i - 1], ipa):
        i -= 1
    return "".join(units[i:])


# --------------------------------------------------------------------------
# Output modes
# --------------------------------------------------------------------------

def print_list(rows, title):
    by_sense = {}
    for r in rows:
        by_sense.setdefault(r["sense"] or "(general)", []).append(r)
    print(f"\n=== {title} ({len(rows)} translations) ===\n")
    for sense, items in by_sense.items():
        print(f"-- meaning: {sense}")
        for r in sorted(items, key=lambda x: x["lang"]):
            extra = []
            if r["roman"]:
                extra.append(f"/{r['roman']}/")
            if r["tags"]:
                extra.append(f"[{r['tags']}]")
            tail = ("  " + " ".join(extra)) if extra else ""
            print(f"   {r['lang']:<22} {r['word']}{tail}")
        print()


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv_module.DictWriter(
            f, fieldnames=["sense", "lang", "word", "roman", "tags"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} rows to {path}")


def _top(counter, n):
    total = sum(counter.values()) or 1
    return [(k, v, 100.0 * v / total) for k, v in counter.most_common(n)]


def print_ipa_list(items):
    print(f"\n=== Real IPA from entries ({len(items)} pronunciations) ===\n")
    for it in sorted(items, key=lambda x: x["lang"]):
        tag = f"  [{it['tags']}]" if it["tags"] else ""
        print(f"   {it['lang']:<18} {it['label']:<16} {it['form']}{tag}")


def print_stats(items, top, ipa=False):
    forms = [to_units(it["form"], ipa) for it in items]
    forms = [f for f in forms if f]
    if not forms:
        sys.exit("No usable forms to analyze "
                 "(in --ipa mode, the data needs a `sounds` field with IPA).")

    sounds, initials, finals = Counter(), Counter(), Counter()
    bigrams, trigrams = Counter(), Counter()
    skeletons, onsets, codas, syls = Counter(), Counter(), Counter(), Counter()

    for f in forms:
        sounds.update(f)
        initials[f[0]] += 1
        finals[f[-1]] += 1
        for i in range(len(f) - 1):
            bigrams["".join(f[i:i + 2])] += 1
        for i in range(len(f) - 2):
            trigrams["".join(f[i:i + 3])] += 1
        skeletons[cv_skeleton(f, ipa)] += 1
        onsets[onset(f, ipa) or "(none)"] += 1
        codas[coda(f, ipa) or "(none)"] += 1
        for s in syllable_split(f, ipa):
            syls[s] += 1

    def block(label, counter):
        print(f"\n{label}")
        for k, v, pct in _top(counter, top):
            bar = "#" * int(pct / 2)
            print(f"   {k:<8} {v:>4}  {pct:4.1f}%  {bar}")

    unit = "phonemes" if ipa else "sounds"
    kind = "real IPA phonemes" if ipa else "romanization-based, approximate"
    print(f"\n=== Sound patterns across {len(forms)} forms ({kind}) ===")
    block(f"Most common {unit}:", sounds)
    block(f"Most common word-INITIAL {unit}:", initials)
    block(f"Most common word-FINAL {unit}:", finals)
    block(f"Most common 2-{unit[:-1]} sequences:", bigrams)
    block(f"Most common 3-{unit[:-1]} sequences:", trigrams)
    block("Most common syllable-like units:", syls)
    block("Most common ONSET clusters:", onsets)
    block("Most common CODA clusters:", codas)
    block("Most common C/V shapes (whole word):", skeletons)


def _inventory_fitter(inventory, ipa):
    """Return (allowed_set, fits(form)->bool) for an inventory string."""
    allowed = set(inventory.replace(",", " ").split())
    if not ipa:
        allowed = {t.lower() for t in allowed}
        multi = sorted((t for t in allowed if len(t) > 1), key=len, reverse=True)
        singles = {t for t in allowed if len(t) == 1}

        def fits(form):
            s = normalize(form)
            i = 0
            while i < len(s):
                for m in multi:
                    if s.startswith(m, i):
                        i += len(m)
                        break
                else:
                    if s[i] in singles:
                        i += 1
                    else:
                        return False
            return True
    else:
        def fits(form):
            for u in to_units(form, True):
                base = unicodedata.normalize("NFD", u)
                base = base[0] if base else u
                if u not in allowed and base not in allowed:
                    return False
            return True
    return allowed, fits


def print_inventory(items, inventory, ipa=False):
    allowed, fits = _inventory_fitter(inventory, ipa)
    keep = [it for it in items if fits(it["form"])]
    print(f"\n=== Forms fitting inventory [{' '.join(sorted(allowed))}] "
          f"({len(keep)} of {len(items)}) ===\n")
    for it in sorted(keep, key=lambda x: x["lang"]):
        print(f"   {it['lang']:<22} {it['form']}")


# --------------------------------------------------------------------------
# Cross-concept comparison
# --------------------------------------------------------------------------

def _form_counters(items, ipa=False):
    sounds, skeletons, syls = Counter(), Counter(), Counter()
    for it in items:
        f = to_units(it["form"], ipa)
        if not f:
            continue
        sounds.update(f)
        skeletons[cv_skeleton(f, ipa)] += 1
        for s in syllable_split(f, ipa):
            syls[s] += 1
    return sounds, skeletons, syls


def print_compare(concept_items, top, ipa=False):
    """concept_items: list of (label, items). Show each concept's signature
    and what sounds/shapes they share vs. that set one apart."""
    print(f"\n=== Comparing {len(concept_items)} concepts "
          f"({'real IPA' if ipa else 'romanization'}) ===")
    per_sounds = {}
    for label, items in concept_items:
        sounds, skeletons, syls = _form_counters(items, ipa)
        per_sounds[label] = sounds
        tot = sum(sounds.values()) or 1
        top_sounds = ", ".join(f"{k}({100 * v // tot}%)"
                               for k, v in sounds.most_common(top))
        top_shapes = ", ".join(f"{k}" for k, _ in skeletons.most_common(5))
        top_syls = ", ".join(f"{k}" for k, _ in syls.most_common(8))
        print(f"\n# {label}  ({len(items)} forms)")
        print(f"   top sounds : {top_sounds}")
        print(f"   top shapes : {top_shapes}")
        print(f"   top syllabs: {top_syls}")

    if len(per_sounds) > 1:
        # Sounds shared prominently across every concept (the 'universal feel'),
        # using each concept's normalized share so big lists don't dominate.
        labels = list(per_sounds)
        shares = {}
        for lab, c in per_sounds.items():
            tot = sum(c.values()) or 1
            shares[lab] = {k: v / tot for k, v in c.items()}
        common = set(shares[labels[0]])
        for lab in labels[1:]:
            common &= set(shares[lab])
        ranked = sorted(common,
                        key=lambda k: min(shares[lab][k] for lab in labels),
                        reverse=True)
        print("\n# Shared across ALL concepts (min share):")
        for k in ranked[:top]:
            mn = min(shares[lab][k] for lab in labels)
            print(f"   {k:<4} {100 * mn:4.1f}%")


# --------------------------------------------------------------------------
# Phonotactic word generator
# --------------------------------------------------------------------------

def build_markov(unit_forms, order=2):
    """Order-N model over units with start/end padding ('^' start, '$' end).

    Units are letters (romanization) or IPA phonemes -- the model is the same.
    """
    model = {}
    for f in unit_forms:
        seq = ["^"] * order + list(f) + ["$"]
        for i in range(len(seq) - order):
            ctx = tuple(seq[i:i + order])
            model.setdefault(ctx, Counter())[seq[i + order]] += 1
    return model, order


def generate_word(model, order, rng, max_units=12):
    import bisect
    ctx = ("^",) * order
    out = []
    for _ in range(max_units):
        counter = model.get(ctx)
        if not counter:
            break
        items = list(counter.items())
        cum, run = [], 0
        for _, c in items:
            run += c
            cum.append(run)
        pick = items[bisect.bisect(cum, rng.random() * run)][0]
        if pick == "$":
            break
        out.append(pick)
        ctx = tuple(list(ctx[1:]) + [pick])
    return out  # list of units


def print_generate(items, n, args, rng, ipa=False):
    forms = [to_units(it["form"], ipa) for it in items]
    forms = [f for f in forms if f]
    if args.inventory:  # train only on words that fit the target phonology
        _, fits = _inventory_fitter(args.inventory, ipa)
        forms = [f for f in forms if fits("".join(f))]
        if not forms:
            sys.exit("No source words fit that inventory; loosen --inventory.")

    lo, hi = args.length
    model, order = build_markov(forms)
    real = {"".join(f) for f in forms}
    seen, results, tries = set(), [], 0
    while len(results) < n and tries < n * 400:
        tries += 1
        units = generate_word(model, order, rng)
        w = "".join(units)
        if (lo <= len(units) <= hi and w not in real and w not in seen
                and any(is_vowel(u, ipa) for u in units)):
            seen.add(w)
            results.append(w)

    kind = "real IPA forms" if ipa else "real translations"
    print(f"\n=== {len(results)} invented words "
          f"(learned from {len(forms)} {kind}) ===\n")
    for w in results:
        print(f"   /{w}/" if ipa else f"   {w}")
    if len(results) < n:
        print(f"\n(only {len(results)} unique words fit the constraints; "
              "try a wider --length or more source data)")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Gather and analyze a concept's translations for conlanging")
    ap.add_argument("word", nargs="?", help="English concept, e.g. thunder "
                    "(downloads data; omit if using --file)")
    ap.add_argument("--file", action="append", metavar="PATH",
                    help="local .jsonl from kaikki.org; repeatable "
                    "(several files enable --compare and pooled --generate)")
    ap.add_argument("--sense", help="only keep translations whose meaning "
                    "contains this text, e.g. 'lightning'")
    ap.add_argument("--csv", metavar="FILE", help="save results to a CSV file")
    ap.add_argument("--stats", action="store_true",
                    help="report sound / syllable patterns")
    ap.add_argument("--compare", action="store_true",
                    help="compare sound signatures across the loaded concepts")
    ap.add_argument("--generate", type=int, metavar="N",
                    help="invent N new words from the learned phonotactics")
    ap.add_argument("--length", metavar="MIN-MAX", default="3-9",
                    help="length range for --generate (default 3-9)")
    ap.add_argument("--seed", type=int, help="random seed for reproducible "
                    "--generate output")
    ap.add_argument("--inventory", metavar="SOUNDS",
                    help="keep only words built from these sounds, e.g. "
                    '"p t k m n s r a i u" (space or comma separated)')
    ap.add_argument("--ipa", action="store_true",
                    help="analyze REAL IPA from each entry's `sounds` field "
                    "(phonemes, not spelling). Best with downloaded word "
                    "entries; translation lists rarely carry IPA.")
    ap.add_argument("--top", type=int, default=15,
                    help="how many entries per stats list (default 15)")
    args = ap.parse_args()

    sources = load_sources(args)
    # Build, per concept: the translation rows and the analysis items.
    concepts = []  # list of (label, items, rows)
    for label, recs in sources:
        rows = collect_translations(recs, args.sense)
        items = collect_ipa(recs, rows) if args.ipa else rows_to_items(rows)
        if items or rows:
            concepts.append((label, items, rows))
    if not concepts:
        sys.exit("No data found (check the word, file, or --sense).")
    all_items = [it for _, items, _ in concepts for it in items]
    all_rows = [r for _, _, rows in concepts for r in rows]
    title = " + ".join(lab for lab, _, _ in concepts)

    if args.ipa and not all_items:
        sys.exit("No IPA found. The data has translations but no `sounds`/IPA. "
                 "Download each word's own entry (which carries IPA) and pass "
                 "them with --file, then re-run with --ipa.")

    if args.compare:
        print_compare([(lab, items) for lab, items, _ in concepts],
                      args.top, args.ipa)
    elif args.generate:
        import random
        try:
            lo, hi = (int(x) for x in args.length.split("-"))
        except ValueError:
            sys.exit("--length must look like MIN-MAX, e.g. 3-9")
        args.length = (lo, hi)
        print_generate(all_items, args.generate, args,
                       random.Random(args.seed), args.ipa)
    elif args.stats:
        print_stats(all_items, args.top, args.ipa)
    elif args.inventory:
        print_inventory(all_items, args.inventory, args.ipa)
    elif args.ipa:
        print_ipa_list(all_items)
    else:
        print_list(all_rows, title)

    if args.csv:
        write_csv(all_rows, args.csv)


if __name__ == "__main__":
    main()
