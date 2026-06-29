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
# Cross-concept comparison
# --------------------------------------------------------------------------

def _form_counters(rows):
    sounds, skeletons, syls = Counter(), Counter(), Counter()
    for r in rows:
        f = normalize(sound_form(r))
        if not f:
            continue
        sounds.update(f)
        skeletons[cv_skeleton(f)] += 1
        for s in syllable_split(f):
            syls[s] += 1
    return sounds, skeletons, syls


def print_compare(concept_rows, top):
    """concept_rows: list of (label, rows). Show each concept's signature
    and what sounds/shapes they share vs. that set one apart."""
    print(f"\n=== Comparing {len(concept_rows)} concepts ===")
    per_sounds = {}
    for label, rows in concept_rows:
        sounds, skeletons, syls = _form_counters(rows)
        per_sounds[label] = sounds
        tot = sum(sounds.values()) or 1
        top_sounds = ", ".join(f"{k}({100 * v // tot}%)"
                               for k, v in sounds.most_common(top))
        top_shapes = ", ".join(f"{k}" for k, _ in skeletons.most_common(5))
        top_syls = ", ".join(f"{k}" for k, _ in syls.most_common(8))
        print(f"\n# {label}  ({len(rows)} translations)")
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

def build_markov(forms, order=2):
    """Order-N character model with start/end padding ('^' start, '$' end)."""
    model = {}
    start = "^" * order
    for f in forms:
        seq = start + f + "$"
        for i in range(len(seq) - order):
            ctx = seq[i:i + order]
            nxt = seq[i + order]
            model.setdefault(ctx, Counter())[nxt] += 1
    return model, order


def generate_word(model, order, rng, max_len=12):
    import bisect
    ctx = "^" * order
    out = []
    for _ in range(max_len):
        counter = model.get(ctx)
        if not counter:
            break
        items = list(counter.items())
        weights, cum, run = [c for _, c in items], [], 0
        for w in weights:
            run += w
            cum.append(run)
        pick = items[bisect.bisect(cum, rng.random() * run)][0]
        if pick == "$":
            break
        out.append(pick)
        ctx = (ctx + pick)[-order:]
    return "".join(out)


def print_generate(rows, n, args, rng):
    forms = [normalize(sound_form(r)) for r in rows]
    forms = [f for f in forms if f]
    if args.inventory:  # train only on words that fit the target phonology
        allowed = set(args.inventory.replace(",", " ").split())
        singles = {t for t in allowed if len(t) == 1}
        multi = sorted((t for t in allowed if len(t) > 1), key=len, reverse=True)

        def fits(s):
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
        forms = [f for f in forms if fits(f)]
        if not forms:
            sys.exit("No source words fit that inventory; loosen --inventory.")

    lo, hi = args.length
    model, order = build_markov(forms)
    real = set(forms)
    seen, results, tries = set(), [], 0
    while len(results) < n and tries < n * 400:
        tries += 1
        w = generate_word(model, order, rng)
        if (lo <= len(w) <= hi and w not in real and w not in seen
                and any(c in VOWELS for c in w)):
            seen.add(w)
            results.append(w)

    print(f"\n=== {len(results)} invented words "
          f"(learned from {len(forms)} real translations) ===\n")
    for w in results:
        print(f"   {w}")
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
    ap.add_argument("--top", type=int, default=15,
                    help="how many entries per stats list (default 15)")
    args = ap.parse_args()

    sources = load_sources(args)
    concept_rows = [(label, collect_translations(recs, args.sense))
                    for label, recs in sources]
    concept_rows = [(lab, rows) for lab, rows in concept_rows if rows]
    if not concept_rows:
        sys.exit("No translations found (check the word, file, or --sense).")
    all_rows = [r for _, rows in concept_rows for r in rows]
    title = " + ".join(lab for lab, _ in concept_rows)

    if args.compare:
        print_compare(concept_rows, args.top)
    elif args.generate:
        import random
        try:
            lo, hi = (int(x) for x in args.length.split("-"))
        except ValueError:
            sys.exit("--length must look like MIN-MAX, e.g. 3-9")
        args.length = (lo, hi)
        print_generate(all_rows, args.generate, args, random.Random(args.seed))
    elif args.stats:
        print_stats(all_rows, args.top)
    elif args.inventory:
        print_inventory(all_rows, args.inventory)
    else:
        print_list(all_rows, title)

    if args.csv:
        write_csv(all_rows, args.csv)


if __name__ == "__main__":
    main()
