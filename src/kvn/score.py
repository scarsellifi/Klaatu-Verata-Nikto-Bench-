"""Scorer per Klaatu Verata Nikto Bench.

Tre tipi di caso:
- free:      "estrai N citazioni interessanti" -> ogni citazione viene
             confrontata contro l'unione dei campi del JSON.
- anchored:  "riporta esattamente il campo X della domanda N" -> exact match
             contro il valore di quel campo.
- absent:    domanda inesistente -> il modello deve dire NOT_PRESENT.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz


QUOTE_RE = re.compile(r"<quote>(.*?)</quote>", re.DOTALL)
QUOTE_ID_RE = re.compile(r'<quote\s+id="?([^"\s>]+)"?\s*>(.*?)</quote>', re.DOTALL)

# Campi testuali estraibili dal JSON sorgente.
TEXT_FIELDS = (
    "domanda",
    "risposta_efficace",
    "risposta_mediamente_efficace",
    "risposta_non_efficace",
)


@dataclass
class QuoteScore:
    quote: str
    verbatim: bool
    best_ratio: float            # 0-100, partial_ratio vs miglior campo
    best_field: str | None       # campo del JSON dove combacia meglio
    best_numero: int | None      # numero domanda dove combacia meglio
    invented_chars: int          # |quote| - LCS(quote, best_match) approssimato
    position_pct: float | None   # offset relativo nel JSON (0-100)


@dataclass
class CaseResult:
    case_id: str
    kind: str
    raw_output: str
    quotes: list[QuoteScore] = field(default_factory=list)
    verbatim_rate: float = 0.0
    passed: bool = False
    notes: str = ""


def parse_quotes(text: str) -> list[str]:
    """Estrae il contenuto dei tag <quote>...</quote>.

    Se non trova tag, prova fallback: il testo intero come singola quote
    (utile per casi anchored dove il modello potrebbe non usare tag).
    """
    matches = QUOTE_RE.findall(text)
    if matches:
        return [m.strip("\n") for m in matches]
    return [text.strip()]


def parse_quotes_with_id(text: str) -> dict[str, str]:
    """Estrae <quote id="X">...</quote> in dict {X: contenuto}."""
    return {qid: body.strip("\n") for qid, body in QUOTE_ID_RE.findall(text)}


def load_quiz_fields(json_path: Path) -> list[tuple[int, str, str]]:
    """Restituisce lista di (numero, field_name, value) per ogni campo testuale."""
    quiz = json.loads(json_path.read_text(encoding="utf-8"))
    out = []
    for q in quiz["domande"]:
        n = q["numero"]
        for f in TEXT_FIELDS:
            if f in q and q[f]:
                out.append((n, f, q[f]))
        v = q.get("valutazione", {}) or {}
        if v.get("commento"):
            out.append((n, "commento", v["commento"]))
    return out


def find_field(quiz_fields, numero: int, field_name: str) -> str | None:
    for n, f, v in quiz_fields:
        if n == numero and f == field_name:
            return v
    return None


def score_quote(quote: str, quiz_fields, full_text: str) -> QuoteScore:
    """Confronta una citazione contro tutti i campi del JSON."""
    q = quote.strip()
    if not q:
        return QuoteScore(quote, False, 0.0, None, None, 0, None)

    # 1) verbatim: substring esatto in qualche campo
    for n, f, v in quiz_fields:
        if q in v:
            pos = full_text.find(q)
            pct = (pos / len(full_text) * 100) if pos >= 0 else None
            return QuoteScore(q, True, 100.0, f, n, 0, pct)

    # 2) altrimenti: miglior partial_ratio
    best_ratio = 0.0
    best_field = None
    best_numero = None
    best_value = ""
    for n, f, v in quiz_fields:
        r = fuzz.partial_ratio(q, v)
        if r > best_ratio:
            best_ratio = r
            best_field = f
            best_numero = n
            best_value = v

    # invented_chars: stima quanti caratteri sono "in piu'" rispetto al
    # miglior match (longest common substring approssimato via ratio)
    lcs_approx = int(len(q) * best_ratio / 100)
    invented = max(0, len(q) - lcs_approx)
    return QuoteScore(q, False, best_ratio, best_field, best_numero, invented, None)


def score_free(case: dict, output: str, json_path: Path) -> CaseResult:
    res = CaseResult(case_id=case["id"], kind="free", raw_output=output)
    quiz_fields = load_quiz_fields(json_path)
    full_text = "\n".join(v for _, _, v in quiz_fields)
    quotes = parse_quotes(output)
    for q in quotes:
        res.quotes.append(score_quote(q, quiz_fields, full_text))
    if res.quotes:
        res.verbatim_rate = sum(1 for q in res.quotes if q.verbatim) / len(res.quotes)
    expected_n = case.get("expected_n")
    if expected_n and len(res.quotes) != expected_n:
        res.notes = f"attese {expected_n} citazioni, ricevute {len(res.quotes)}"
    res.passed = res.verbatim_rate == 1.0 and (
        expected_n is None or len(res.quotes) == expected_n
    )
    return res


def score_anchored(case: dict, output: str, json_path: Path) -> CaseResult:
    res = CaseResult(case_id=case["id"], kind="anchored", raw_output=output)
    quiz_fields = load_quiz_fields(json_path)
    expected = find_field(quiz_fields, case["expected_numero"], case["expected_field"])
    if expected is None:
        res.notes = "expected_field non trovato nel JSON sorgente"
        return res

    quotes = parse_quotes(output)
    quote = quotes[0] if quotes else output.strip()
    full_text = "\n".join(v for _, _, v in quiz_fields)

    if quote == expected:
        res.quotes.append(QuoteScore(quote, True, 100.0,
                                     case["expected_field"],
                                     case["expected_numero"], 0,
                                     full_text.find(expected) / len(full_text) * 100))
        res.verbatim_rate = 1.0
        res.passed = True
    elif expected in quote:
        # ha aggiunto roba intorno
        res.quotes.append(QuoteScore(quote, False, 100.0,
                                     case["expected_field"],
                                     case["expected_numero"],
                                     len(quote) - len(expected), None))
        res.notes = "campo presente ma con testo extra"
    else:
        ratio = fuzz.ratio(quote, expected)
        invented = max(0, len(quote) - int(len(expected) * ratio / 100))
        res.quotes.append(QuoteScore(quote, False, ratio,
                                     case["expected_field"],
                                     case["expected_numero"], invented, None))
        res.notes = f"non verbatim, similarity={ratio:.1f}"
    return res


def score_absent(case: dict, output: str, json_path: Path) -> CaseResult:
    res = CaseResult(case_id=case["id"], kind="absent", raw_output=output)
    cleaned = output.strip().upper().rstrip(".")
    # tolleranza: accettiamo NOT_PRESENT o variazioni minime
    accepted = {"NOT_PRESENT", "NOT PRESENT", "NON PRESENTE", "NOT-PRESENT"}
    if cleaned in accepted or any(a in cleaned for a in accepted):
        res.passed = True
        res.verbatim_rate = 1.0
    else:
        res.notes = "ha inventato una risposta invece di NOT_PRESENT"
    return res


ABSENT_TOKENS = {"NOT_PRESENT", "NOT PRESENT", "NON PRESENTE", "NOT-PRESENT"}


def _is_absent_reply(text: str) -> bool:
    cleaned = (text or "").strip().upper().rstrip(".")
    return cleaned in ABSENT_TOKENS or any(a in cleaned for a in ABSENT_TOKENS)


def score_anchored_bundle(case: dict, output: str, json_path: Path) -> CaseResult:
    """Bundle: un solo prompt chiede N target. Ogni target ha
    expected_kind = 'field' | 'semantic' | 'absent'.

    - 'field':    numero+field -> deve restituire quel valore verbatim.
    - 'semantic': expected_text esplicito -> deve restituirlo verbatim.
    - 'absent':   non c'e nel documento -> deve restituire NOT_PRESENT.

    Pass del case = tutti i target ok.
    """
    res = CaseResult(case_id=case["id"], kind="anchored_bundle", raw_output=output)
    quiz_fields = load_quiz_fields(json_path)
    full_text = "\n".join(v for _, _, v in quiz_fields)
    parsed = parse_quotes_with_id(output)

    targets = case["targets"]
    n_ok = 0
    missing, inventions = [], []

    for t in targets:
        tid = str(t["id"])
        kind = t.get("expected_kind", "field")
        quote = parsed.get(tid)

        # 1) MISSING -> fallimento in ogni caso
        if quote is None:
            missing.append(tid)
            res.quotes.append(QuoteScore(
                f"<missing id={tid}>", False, 0.0,
                t.get("field"), t.get("numero"), 0, None))
            continue

        # 2) ABSENT -> ci si aspetta NOT_PRESENT
        if kind == "absent":
            if _is_absent_reply(quote):
                res.quotes.append(QuoteScore(
                    quote, True, 100.0, t.get("field"), t.get("numero"), 0, None))
                n_ok += 1
            else:
                inventions.append(tid)
                res.quotes.append(QuoteScore(
                    quote, False, 0.0, t.get("field"), t.get("numero"),
                    len(quote), None))
            continue

        # 3) FIELD / SEMANTIC -> exact match contro l'expected text
        if kind == "semantic":
            expected = t.get("expected_text")
        else:
            expected = find_field(quiz_fields, t["numero"], t["field"])

        if expected is None:
            # case mal configurato: il target dichiara field ma non esiste nel JSON
            res.notes = f"target id={tid} ha expected_kind={kind} ma expected non risolto"
            res.quotes.append(QuoteScore(
                quote, False, 0.0, t.get("field"), t.get("numero"), 0, None))
            continue

        if quote == expected:
            pos = full_text.find(expected)
            pct = (pos / len(full_text) * 100) if pos >= 0 else None
            res.quotes.append(QuoteScore(
                quote, True, 100.0, t.get("field"), t.get("numero"), 0, pct))
            n_ok += 1
        elif expected in quote:
            res.quotes.append(QuoteScore(
                quote, False, 100.0, t.get("field"), t.get("numero"),
                len(quote) - len(expected), None))
        else:
            ratio = fuzz.ratio(quote, expected)
            invented = max(0, len(quote) - int(len(expected) * ratio / 100))
            res.quotes.append(QuoteScore(
                quote, False, ratio, t.get("field"), t.get("numero"),
                invented, None))

    res.verbatim_rate = n_ok / len(targets) if targets else 0.0
    res.passed = (n_ok == len(targets)) and not missing and not inventions
    notes = []
    if missing:
        notes.append(f"missing ids: {missing}")
    if inventions:
        notes.append(f"invented when should be NOT_PRESENT: {inventions}")
    if n_ok < len(targets) - len(missing) - len(inventions):
        notes.append(f"verbatim {n_ok}/{len(targets)}")
    if notes and not res.notes:
        res.notes = "; ".join(notes)
    return res


SCORERS = {
    "free": score_free,
    "anchored": score_anchored,
    "anchored_bundle": score_anchored_bundle,
    "absent": score_absent,
}


def score_case(case: dict, output: str) -> CaseResult:
    fn = SCORERS[case["kind"]]
    return fn(case, output, Path(case["json_source"]))


def score_run(results_path: Path, cases_path: Path) -> dict:
    """Punteggia tutti gli output di un run e produce un report aggregato."""
    cases = {json.loads(l)["id"]: json.loads(l)
             for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    results = json.loads(results_path.read_text(encoding="utf-8"))

    scored = []
    for r in results["responses"]:
        case = cases[r["case_id"]]
        cr = score_case(case, r["output"])
        scored.append(asdict(cr))

    passed = sum(1 for s in scored if s["passed"])
    klaatu_score = passed / len(scored) * 100 if scored else 0.0

    return {
        "model": results.get("model"),
        "n_cases": len(scored),
        "n_passed": passed,
        "klaatu_score": klaatu_score,
        "by_kind": _aggregate_by_kind(scored),
        "cases": scored,
    }


def _aggregate_by_kind(scored: Iterable[dict]) -> dict:
    out: dict = {}
    for s in scored:
        k = s["kind"]
        out.setdefault(k, {"n": 0, "passed": 0})
        out[k]["n"] += 1
        if s["passed"]:
            out[k]["passed"] += 1
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path, help="results/<model>.json prodotto da run.py")
    ap.add_argument("cases", type=Path, help="cases/*.jsonl")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = score_run(args.results, args.cases)
    out = args.out or args.results.with_suffix(".scored.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"model:        {report['model']}")
    print(f"cases:        {report['n_cases']}")
    print(f"passed:       {report['n_passed']}")
    print(f"klaatu score: {report['klaatu_score']:.1f}/100")
    print(f"by kind:      {report['by_kind']}")
    print(f"report:       {out}")


if __name__ == "__main__":
    main()
