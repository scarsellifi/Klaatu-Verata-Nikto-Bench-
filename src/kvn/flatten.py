"""Trasforma un quiz JSON in testo piatto leggibile.

I marker strutturali ('--- Domanda N ---', 'DOMANDA:', etc.) sono introdotti
da noi e sono prevedibili: lo scorer deve confrontare le citazioni del
modello con i campi originali del JSON, non con il testo flat.
I contenuti (domanda, risposte, commento) sono copiati verbatim dal JSON.
"""

import argparse
import json
from pathlib import Path


def flatten(quiz: dict) -> str:
    out = []
    titolo = quiz.get("titolo", "")
    out.append("=" * 72)
    out.append(titolo)
    out.append("=" * 72)
    out.append("")

    for q in quiz["domande"]:
        n = q["numero"]
        out.append(f"--- Domanda {n} ---")
        out.append("")
        out.append("DOMANDA:")
        out.append(q["domanda"])
        out.append("")
        out.append("RISPOSTA EFFICACE:")
        out.append(q["risposta_efficace"])
        out.append("")
        out.append("RISPOSTA MEDIAMENTE EFFICACE:")
        out.append(q["risposta_mediamente_efficace"])
        out.append("")
        out.append("RISPOSTA NON EFFICACE:")
        out.append(q["risposta_non_efficace"])
        out.append("")
        v = q.get("valutazione", {})
        if v:
            out.append("VALUTAZIONE:")
            if "difficolta" in v:
                out.append(f"Difficolta: {v['difficolta']}")
            if "completezza" in v:
                out.append(f"Completezza: {v['completezza']}")
            if "commento" in v:
                out.append("Commento:")
                out.append(v["commento"])
        out.append("")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--limit", type=int, default=None,
                    help="Tronca alle prime N domande")
    args = ap.parse_args()

    quiz = json.loads(args.input.read_text(encoding="utf-8"))
    if args.limit:
        quiz["domande"] = quiz["domande"][:args.limit]
    text = flatten(quiz)
    args.output.write_text(text, encoding="utf-8")

    print(f"input:  {args.input}")
    print(f"output: {args.output}")
    print(f"chars:  {len(text):,}")
    print(f"lines:  {text.count(chr(10)):,}")
    print(f"~tok:   {len(text)//4:,}")


if __name__ == "__main__":
    main()
