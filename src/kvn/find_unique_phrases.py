"""Trova frasi distintive (presenti in un solo campo del JSON) da usare
come 'semantic anchors' nei casi del benchmark.

Una frase e' un buon anchor semantico se:
- e' lunga abbastanza da non essere ambigua (>= 6 parole);
- appare in **esattamente un** campo testuale del JSON;
- non contiene caratteri strani (newline, tag, ecc.).

Output: per ciascun campo testuale richiesto, una frase candidata.
"""

import argparse
import json
import re
from pathlib import Path

TEXT_FIELDS = ("domanda", "risposta_efficace",
               "risposta_mediamente_efficace", "risposta_non_efficace")


def all_fields(quiz):
    out = []
    for q in quiz["domande"]:
        n = q["numero"]
        for f in TEXT_FIELDS:
            if q.get(f):
                out.append((n, f, q[f]))
        v = (q.get("valutazione") or {}).get("commento")
        if v:
            out.append((n, "commento", v))
    return out


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 6]


def find_unique(all_text_fields, target_numero, target_field, target_value):
    """Trova una sotto-frase di target_value (preferendo NON l'incipit) che
    compare in un solo campo, cosi' il modello deve davvero leggere il testo
    e non solo cercare il primo paragrafo."""
    sentences = split_sentences(target_value)
    # provo prima le frasi dopo la prima (anchor "interno")
    order = sentences[1:] + sentences[:1]
    for s in order:
        probe = s.rstrip(".!?,;:").strip()
        if len(probe) < 30 or len(probe.split()) < 6:
            continue
        hits = sum(1 for (_, _, v) in all_text_fields if probe in v)
        if hits == 1:
            return probe
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_source", type=Path)
    ap.add_argument("--max", type=int, default=300,
                    help="prime N domande da scandire (per restare nel _30 o _90)")
    ap.add_argument("--n", type=int, default=10,
                    help="quanti anchor semantici trovare")
    args = ap.parse_args()

    quiz = json.loads(args.json_source.read_text(encoding="utf-8"))
    quiz["domande"] = quiz["domande"][:args.max]
    fields = all_fields(quiz)

    found = []
    used_numeros = set()
    for (n, f, v) in fields:
        if f == "domanda":
            continue
        if n in used_numeros:  # max 1 anchor per numero, per varieta'
            continue
        probe = find_unique(fields, n, f, v)
        if probe:
            found.append({
                "numero": n,
                "field": f,
                "anchor_phrase": probe,
                "expected_text": v,
            })
            used_numeros.add(n)
        if len(found) >= args.n:
            break

    print(json.dumps(found, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
