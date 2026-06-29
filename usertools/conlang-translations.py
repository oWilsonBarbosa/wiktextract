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
#
# NOTE ON "SOUNDS": translations rarely carry full IPA, so the sound
# analysis uses each word's romanization (or the word itself for
# Latin-script languages), with accent/diacritic marks stripped. That is
# an approximation of pronunciation, not a true phonemic transcription --
# good enough for spotting cross-linguistic tendencies and for sifting
# words that fit a phoneme inventory, but treat it as inspiration, not gospel.

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


def load_records(args):
    """Return the list of word records, from a local file or by download."""
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    if not args.word:
        sys.exit("Give a word, or --file a downloaded .jsonl. See --help.")
    url = kaikki_url(args.word)
    req = urllib.request.Request(url, headers={"User-Agent": "conlang-helper"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"No data found for '{args.word}' at {url}")
        sys.exit(f"Download failed ({e.code}): {url}")
    except Exception as e:
        sys.exit(f"Could not reach kaikki.org: {e}\n"
                 "Tip: download the .jsonl by hand and use --file instead.")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


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
                "tags": ", ".join(tr.get("tags", [])),
            })
    return rows


# --------------------------------------------------------------------------
# Sound handling (approximate, from romanization)
# --------------------------------------------------------------------------

VOWELS = set("aeiou")


def sound_form(row):
    """Best available 'how it sounds' string: romanization, else the word
    itself when it is already in Latin letters."""
    if row["roman"]:
        return row["roman"]
    return row["word"]


def normalize(text):
    """Lowercase, strip diacritics/accents, drop anything but letters.

    Returns a simple a-z-ish letter string approximating the sounds.
    """
    text = text.lower()
    # take only the first variant if several are slash/comma separated
    for sep in ("/", ",", ";"):
        if sep in text:
            text = text.split(sep)[0]
    # decompose accents and drop the combining marks
    text = unicodedata.normalize("NFD", text)
    out = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if ch.isalpha():
            out.append(ch)
        # spaces, hyphens, apostrophes, digits -> treated as breaks/ignored
    return "".join(out)


def cv_skeleton(letters):
    """Map a letter string to a C/V skeleton, e.g. 'grom' -> 'CCVC'."""
    return "".join("V" if c in VOWELS else "C" for c in letters)


def syllable_split(letters):
    """Very rough syllable split: each vowel group is a nucleus; trailing
    consonants attach to the preceding nucleus. Good enough for tallying."""
    sylls, cur, seen_vowel = [], "", False
    for c in letters:
        is_v = c in VOWELS
        if is_v and seen_vowel and not (cur and cur[-1] in VOWELS):
            # new nucleus after a consonant boundary -> new syllable started
            sylls.append(cur)
            cur, seen_vowel = c, True
        elif is_v and seen_vowel and cur and cur[-1] in VOWELS:
            cur += c  # vowel cluster, same nucleus
        else:
            if is_v:
                seen_vowel = True
            elif seen_vowel:
                # consonant after a completed nucleus: peek if a vowel follows
                pass
            cur += c
    if cur:
        sylls.append(cur)
    return [s for s in sylls if s]


def onset(letters):
    """Leading consonant cluster (may be empty)."""
    i = 0
    while i < len(letters) and letters[i] not in VOWELS:
        i += 1
    return letters[:i]


def coda(letters):
    """Trailing consonant cluster (may be empty)."""
    i = len(letters)
    while i > 0 and letters[i - 1] not in VOWELS:
        i -= 1
    return letters[i:]


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


def print_stats(rows, top):
    forms = [normalize(sound_form(r)) for r in rows]
    forms = [f for f in forms if f]
    if not forms:
        sys.exit("No usable romanizations to analyze.")

    sounds, initials, finals = Counter(), Counter(), Counter()
    bigrams, trigrams = Counter(), Counter()
    skeletons, onsets, codas, syls = Counter(), Counter(), Counter(), Counter()

    for f in forms:
        sounds.update(f)
        initials[f[0]] += 1
        finals[f[-1]] += 1
        for i in range(len(f) - 1):
            bigrams[f[i:i + 2]] += 1
        for i in range(len(f) - 2):
            trigrams[f[i:i + 3]] += 1
        skeletons[cv_skeleton(f)] += 1
        onsets[onset(f) or "(none)"] += 1
        codas[coda(f) or "(none)"] += 1
        for s in syllable_split(f):
            syls[s] += 1

    def block(label, counter):
        print(f"\n{label}")
        for k, v, pct in _top(counter, top):
            bar = "#" * int(pct / 2)
            print(f"   {k:<8} {v:>4}  {pct:4.1f}%  {bar}")

    print(f"\n=== Sound patterns across {len(forms)} translations "
          f"(romanization-based, approximate) ===")
    block("Most common sounds (letters):", sounds)
    block("Most common word-INITIAL sounds:", initials)
    block("Most common word-FINAL sounds:", finals)
    block("Most common 2-sound sequences:", bigrams)
    block("Most common 3-sound sequences:", trigrams)
    block("Most common syllable-like units:", syls)
    block("Most common consonant ONSET clusters:", onsets)
    block("Most common consonant CODA clusters:", codas)
    block("Most common C/V shapes (whole word):", skeletons)


def print_inventory(rows, inventory):
    allowed = set()
    for token in inventory.replace(",", " ").split():
        allowed.add(token.lower())
    # multi-letter tokens are matched greedily; single letters via set
    multi = sorted((t for t in allowed if len(t) > 1), key=len, reverse=True)
    singles = {t for t in allowed if len(t) == 1}

    def fits(letters):
        i = 0
        while i < len(letters):
            for m in multi:
                if letters.startswith(m, i):
                    i += len(m)
                    break
            else:
                if letters[i] in singles:
                    i += 1
                else:
                    return False
        return True

    keep = []
    for r in rows:
        if fits(normalize(sound_form(r))):
            keep.append(r)
    print(f"\n=== Translations fitting inventory "
          f"[{' '.join(sorted(allowed))}] "
          f"({len(keep)} of {len(rows)}) ===\n")
    for r in sorted(keep, key=lambda x: x["lang"]):
        shown = r["roman"] or r["word"]
        print(f"   {r['lang']:<22} {shown}")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Gather and analyze a concept's translations for conlanging")
    ap.add_argument("word", nargs="?", help="English concept, e.g. thunder "
                    "(downloads data; omit if using --file)")
    ap.add_argument("--file", help="local .jsonl downloaded from kaikki.org")
    ap.add_argument("--sense", help="only keep translations whose meaning "
                    "contains this text, e.g. 'lightning'")
    ap.add_argument("--csv", metavar="FILE", help="save results to a CSV file")
    ap.add_argument("--stats", action="store_true",
                    help="report sound / syllable patterns")
    ap.add_argument("--inventory", metavar="SOUNDS",
                    help="keep only words built from these sounds, e.g. "
                    '"p t k m n s r a i u" (space or comma separated)')
    ap.add_argument("--top", type=int, default=15,
                    help="how many entries per stats list (default 15)")
    args = ap.parse_args()

    records = load_records(args)
    rows = collect_translations(records, args.sense)
    if not rows:
        sys.exit("No translations found (check the word, file, or --sense).")

    title = args.word or (args.file or "translations")
    if args.stats:
        print_stats(rows, args.top)
    elif args.inventory:
        print_inventory(rows, args.inventory)
    else:
        print_list(rows, title)

    if args.csv:
        write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
