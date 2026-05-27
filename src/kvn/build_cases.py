"""Genera cases/anchored_only.jsonl, un singolo bundle con target misti:

- positional: chiede field X di Domanda N (verbatim).
- semantic:   chiede 'il campo che contiene la frase Y' (verbatim).
- absent:     chiede qualcosa che non c'e' nel documento (NOT_PRESENT).

Output: 1 sola chiamata per modello per testare ~15-20 anchor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .find_unique_phrases import all_fields, find_unique


# Profili di anchor: il helper sceglie il profilo in base a --max-numero.

POSITIONAL_30 = [
    {"numero": 2,  "field": "risposta_efficace"},
    {"numero": 4,  "field": "risposta_mediamente_efficace"},
    {"numero": 6,  "field": "risposta_non_efficace"},
    {"numero": 8,  "field": "commento"},
    {"numero": 11, "field": "domanda"},
    {"numero": 13, "field": "risposta_efficace"},
    {"numero": 16, "field": "risposta_non_efficace"},
    {"numero": 19, "field": "risposta_mediamente_efficace"},
    {"numero": 22, "field": "commento"},
    {"numero": 24, "field": "domanda"},
    {"numero": 27, "field": "risposta_efficace"},
    {"numero": 29, "field": "risposta_non_efficace"},
]

ABSENT_30 = [
    {"reason": "numero 99 non esiste nel JSON", "numero": 99, "field": "risposta_efficace"},
    {"reason": "Q75 fuori range del file _30", "numero": 75, "field": "risposta_efficace"},
    {"reason": "campo 'firma' non esiste su Q10", "numero": 10, "field": "firma"},
    {"reason": "campo 'telefono' non esiste su Q5", "numero": 5, "field": "telefono"},
    {"reason": "numero 500 non esiste nel JSON", "numero": 500, "field": "commento"},
]

# Profilo per documento integrale (297 domande): anchor a varia profondita',
# pensati per stressare l'attenzione lungo tutto il contesto.
POSITIONAL_FULL = [
    {"numero": 5,   "field": "risposta_efficace"},
    {"numero": 22,  "field": "commento"},
    {"numero": 47,  "field": "risposta_non_efficace"},
    {"numero": 60,  "field": "risposta_mediamente_efficace"},
    {"numero": 88,  "field": "domanda"},
    {"numero": 110, "field": "risposta_efficace"},
    {"numero": 135, "field": "commento"},
    {"numero": 150, "field": "risposta_non_efficace"},
    {"numero": 175, "field": "risposta_mediamente_efficace"},
    {"numero": 200, "field": "domanda"},
    {"numero": 225, "field": "risposta_efficace"},
    {"numero": 250, "field": "commento"},
    {"numero": 270, "field": "risposta_non_efficace"},
    {"numero": 290, "field": "risposta_mediamente_efficace"},
]

ABSENT_FULL = [
    {"reason": "numero 500 non esiste nel JSON", "numero": 500, "field": "risposta_efficace"},
    {"reason": "numero 298 e' subito oltre il range", "numero": 298, "field": "domanda"},
    {"reason": "numero 0 non esiste nel JSON", "numero": 0, "field": "commento"},
    {"reason": "campo 'firma' non esiste su Q150", "numero": 150, "field": "firma"},
    {"reason": "campo 'telefono' non esiste su Q47", "numero": 47, "field": "telefono"},
    {"reason": "campo 'indirizzo' non esiste su Q200", "numero": 200, "field": "indirizzo"},
]

# Quanti anchor semantici generare (ruotando i tipi di campo per varieta').
SEMANTIC_COUNT = 12
SEMANTIC_FIELDS_CYCLE = [
    "risposta_efficace",
    "risposta_mediamente_efficace",
    "risposta_non_efficace",
    "commento",
]


def build_bundle(json_source: Path, text_file: Path, max_numero: int) -> dict:
    quiz = json.loads(json_source.read_text(encoding="utf-8"))
    quiz_subset = dict(quiz)
    quiz_subset["domande"] = quiz["domande"][:max_numero]
    fields = all_fields(quiz_subset)
    by_id = {(n, f): v for (n, f, v) in fields}

    # Scegli il profilo in base alla dimensione del subset
    if max_numero <= 30:
        positional, absent_list, semantic_n = POSITIONAL_30, ABSENT_30, SEMANTIC_COUNT
    else:
        positional, absent_list, semantic_n = POSITIONAL_FULL, ABSENT_FULL, SEMANTIC_COUNT

    targets = []
    instr_lines = [
        "Per ciascun item numerato, restituisci il testo richiesto incapsulato in un blocco <quote id=\"N\">...</quote>, dove N e' il numero dell'item.",
        "Regole assolute:",
        "- copia il testo carattere per carattere, senza alcuna modifica (niente correzioni, niente normalizzazione, niente parafrasi);",
        "- se l'item richiede qualcosa che non e' presente nel documento, restituisci <quote id=\"N\">NOT_PRESENT</quote>;",
        "- non aggiungere alcun testo fuori dai blocchi <quote>.",
        "",
        "Items:",
    ]
    tid = 0

    # 1) Positional
    for p in positional:
        tid += 1
        v = by_id.get((p["numero"], p["field"]))
        if v is None:
            raise SystemExit(f"positional non risolto: Q{p['numero']} {p['field']}")
        targets.append({
            "id": str(tid),
            "expected_kind": "field",
            "numero": p["numero"],
            "field": p["field"],
        })
        instr_lines.append(
            f"{tid}) il testo esatto del campo '{p['field']}' della Domanda {p['numero']}."
        )

    # 2) Semantic: ruota i tipi di campo per evitare di prendere tutto da
    # risposta_efficace. Stratifica i numero per profondita' nel documento.
    used = {p["numero"] for p in positional}
    fields_dict = {(n, f): v for (n, f, v) in fields}
    semantic_count = 0
    cycle_idx = 0
    sorted_nums = sorted({n for (n, _, _) in fields if n not in used})
    if sorted_nums and semantic_n > 1:
        # campiona con passo costante per coprire tutto il range
        stride = max(1, len(sorted_nums) // semantic_n)
        candidate_numeros = sorted_nums[::stride]
    else:
        candidate_numeros = sorted_nums

    for n in candidate_numeros:
        if semantic_count >= semantic_n:
            break
        # prova i tipi di campo in ordine partendo dal prossimo nel ciclo
        chosen = None
        for off in range(len(SEMANTIC_FIELDS_CYCLE)):
            f = SEMANTIC_FIELDS_CYCLE[(cycle_idx + off) % len(SEMANTIC_FIELDS_CYCLE)]
            v = fields_dict.get((n, f))
            if not v:
                continue
            probe = find_unique(fields, n, f, v)
            if probe:
                chosen = (f, v, probe)
                cycle_idx = (cycle_idx + off + 1) % len(SEMANTIC_FIELDS_CYCLE)
                break
        if not chosen:
            continue
        f, v, probe = chosen
        tid += 1
        used.add(n)
        targets.append({
            "id": str(tid),
            "expected_kind": "semantic",
            "numero": n,
            "field": f,
            "anchor_phrase": probe,
            "expected_text": v,
        })
        instr_lines.append(
            f"{tid}) il campo '{f}' (testo integrale) della Domanda nel documento che contiene esattamente questa frase: \"{probe}\"."
        )
        semantic_count += 1

    # 3) Absent
    for a in absent_list:
        tid += 1
        targets.append({
            "id": str(tid),
            "expected_kind": "absent",
            "numero": a["numero"],
            "field": a["field"],
            "reason": a["reason"],
        })
        if a["field"] == "firma":
            instr_lines.append(
                f"{tid}) il testo esatto del campo '{a['field']}' della Domanda {a['numero']}."
            )
        else:
            instr_lines.append(
                f"{tid}) il testo esatto del campo '{a['field']}' della Domanda {a['numero']}."
            )

    instruction = "\n".join(instr_lines)

    return {
        "id": "anchored_bundle_v2_001",
        "kind": "anchored_bundle",
        "text_file": str(text_file),
        "json_source": str(json_source),
        "instruction": instruction,
        "targets": targets,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-source", type=Path,
                    default=Path("data/stabilizzazione_pnrr_addetti_upp_evaluated.json"))
    ap.add_argument("--text-file", type=Path,
                    default=Path("data/texts/stabilizzazione_pnrr_addetti_upp_30.txt"))
    ap.add_argument("--max-numero", type=int, default=30,
                    help="limite massimo di domanda visibile (coerente col text-file)")
    ap.add_argument("--out", type=Path,
                    default=Path("cases/anchored_only.jsonl"))
    args = ap.parse_args()

    case = build_bundle(args.json_source, args.text_file, args.max_numero)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"scritto: {args.out}")
    print(f"target totali: {len(case['targets'])}")
    by_kind = {}
    for t in case["targets"]:
        by_kind[t["expected_kind"]] = by_kind.get(t["expected_kind"], 0) + 1
    print(f"  breakdown: {by_kind}")


if __name__ == "__main__":
    main()
