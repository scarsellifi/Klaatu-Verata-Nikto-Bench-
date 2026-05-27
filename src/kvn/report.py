"""Report HTML autonomo per Klaatu Verata Nikto Bench.

Aggrega N file .scored.json e produce un singolo HTML con:
- pannello hero con statistiche globali;
- card per modello con breakdown per kind, costo, latenza, tipo dominante di errore;
- heatmap di difficolta' per anchor (quali sono "duri" per piu' modelli);
- tassonomia dei failure mode;
- "specimens": diff char-level di ogni failure.

Estetica: scriptorium editoriale - serif classici, palette inchiostro/pergamena.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import html
import json
import mimetypes
import statistics
from datetime import datetime
from pathlib import Path

from .score import load_quiz_fields, find_field


def _image_data_uri(path: Path) -> str:
    """Codifica un'immagine come data: URI (per HTML self-contained)."""
    if not path.exists():
        return ""
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _copy_asset_alongside(src: Path, out_html: Path) -> str | None:
    """Copia un asset accanto all'HTML di output; ritorna il path relativo
    da usare in src=... (None se il file sorgente non esiste).

    Usato per asset grandi (immagini multi-MB) che non conviene embeddare
    in base64 nella HTML."""
    if not src.exists():
        return None
    target = out_html.parent / src.name
    if target.resolve() != src.resolve():
        target.write_bytes(src.read_bytes())
    return src.name


# ---------------------------------------------------------------------------
# Diff char-level
# ---------------------------------------------------------------------------

def diff_html(source: str, model_quote: str) -> str:
    sm = difflib.SequenceMatcher(None, source, model_quote, autojunk=False)
    parts = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        s = html.escape(source[i1:i2])
        m = html.escape(model_quote[j1:j2])
        if op == "equal":
            parts.append(f'<span class="eq">{m}</span>')
        elif op == "replace":
            parts.append(f'<del>{s}</del><ins>{m}</ins>')
        elif op == "delete":
            parts.append(f'<del>{s}</del>')
        elif op == "insert":
            parts.append(f'<ins>{m}</ins>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Failure mode taxonomy
# ---------------------------------------------------------------------------

def classify_failure(quote: dict, target_kind: str) -> str | None:
    """Categorizza UN failure di un anchor.

    paraphrase_micro: quasi identico ma con piccole modifiche (>=95% ratio, <=8 invented)
    paraphrase_macro: parafrasi piu' estesa (80-95%)
    truncation:       contenuto verbatim ma incompleto (alto ratio, 0 invented, lunghezza diversa)
    wrong_anchor:     restituito contenuto da un anchor diverso (ratio 60-80)
    hallucination:    inventato testo non presente (ratio <60)
    invent_on_absent: doveva dire NOT_PRESENT ma ha inventato
    missing:          il modello non ha emesso quel tag <quote id=N>
    """
    if quote["verbatim"]:
        return None
    if target_kind == "absent":
        return "invent_on_absent"
    if str(quote.get("quote", "")).startswith("<missing id="):
        return "missing"
    ratio = quote.get("best_ratio") or 0
    invented = quote.get("invented_chars") or 0
    if ratio >= 95 and invented == 0:
        return "truncation"
    if ratio >= 95:
        return "paraphrase_micro"
    if ratio >= 80:
        return "paraphrase_macro"
    if ratio >= 60:
        return "wrong_anchor"
    return "hallucination"


FAILURE_LABELS = {
    "paraphrase_micro": "micro-paraphrase",
    "paraphrase_macro": "macro-paraphrase",
    "truncation":       "truncation",
    "wrong_anchor":     "wrong anchor",
    "hallucination":    "hallucination",
    "invent_on_absent": "invented (should be NOT_PRESENT)",
    "missing":          "missing tag",
}

# Pesi di gravita' usati per il severity score multi-fattore.
# Una micro-paraphrase vale 1; una invenzione su un campo inesistente vale 10.
SEVERITY_WEIGHTS = {
    "paraphrase_micro": 1,
    "paraphrase_macro": 3,
    "truncation":       2,
    "wrong_anchor":     8,
    "hallucination":    10,
    "invent_on_absent": 10,
    "missing":          5,
}

# Failure modes che un revisore umano non rileverebbe senza un diff.
# Il "silent rate" e' una metrica di rischio: alto = pericoloso in pipeline
# dove il fallimento di una citazione non viene controllato manualmente.
SILENT_MODES = {"paraphrase_micro", "truncation"}

FAILURE_DESCRIPTIONS = {
    "paraphrase_micro": "Almost identical to the source: a punctuation change, a synonym swap, a comma added. The most insidious failure — invisible without a diff.",
    "paraphrase_macro": "A more substantial reword. Same meaning, different sentence shape. Recognizable as the same content.",
    "truncation":       "Verbatim but incomplete. The model copied correctly up to a point and stopped, often dropping a closing clause or final question.",
    "wrong_anchor":     "Returned the right kind of content but from a different question. A context-tracking failure, not a copy failure.",
    "hallucination":    "Returned text that doesn't appear in the source. Pure invention.",
    "invent_on_absent": "The requested field doesn't exist; the model invented a plausible answer instead of saying NOT_PRESENT.",
    "missing":          "The model never emitted that anchor's <quote> tag. Either skipped silently or formatted the output incorrectly.",
}


# ---------------------------------------------------------------------------
# Enrichment & aggregation
# ---------------------------------------------------------------------------

def enrich_with_sources(scored: dict, cases_by_id: dict, quiz_cache: dict) -> dict:
    for case in scored["cases"]:
        case_meta = cases_by_id.get(case["case_id"], {})
        json_source = case_meta.get("json_source")
        if not json_source:
            continue
        if json_source not in quiz_cache:
            quiz_cache[json_source] = load_quiz_fields(Path(json_source))
        quiz_fields = quiz_cache[json_source]

        # mappa id_target -> definizione (per bundles)
        targets_by_id = {}
        for t in case_meta.get("targets", []):
            targets_by_id[str(t["id"])] = t

        expected_text = None
        if case_meta.get("expected_numero") is not None and case_meta.get("expected_field"):
            expected_text = find_field(
                quiz_fields, case_meta["expected_numero"], case_meta["expected_field"]
            )
        case["expected_text"] = expected_text
        case["instruction"] = case_meta.get("instruction", "")
        case["targets_by_id"] = targets_by_id

        for i, q in enumerate(case["quotes"], 1):
            target = targets_by_id.get(str(i)) or {}
            target_kind = target.get("expected_kind") or case.get("kind", "anchored")
            q["target_kind"] = target_kind
            q["target_anchor"] = target.get("anchor_phrase")
            q["target_reason"] = target.get("reason")
            q["failure_mode"] = classify_failure(q, target_kind)

            if q["verbatim"]:
                q["diff_html"] = None
                q["source_text"] = None
                continue

            src = None
            if target_kind == "semantic":
                src = target.get("expected_text")
            elif target_kind == "absent":
                src = "NOT_PRESENT"
            elif expected_text is not None:
                src = expected_text
            elif q.get("best_numero") and q.get("best_field"):
                src = find_field(quiz_fields, q["best_numero"], q["best_field"])
            q["source_text"] = src
            q["diff_html"] = diff_html(src, q["quote"]) if src else None
    return scored


def aggregate_model(scored: dict) -> dict:
    """Calcola metriche derivate per il pannello del modello.

    Oltre al verbatim_rate (binario), calcola:
    - soft_fidelity:  media di best_ratio su TUTTI gli anchor (premia gli
                      "errori vicini" rispetto a quelli grossolani).
    - damage:         caratteri inventati totali (volume di contaminazione).
    - severity_score: somma pesata della gravita' dei failure mode.
    - silent_rate:    % di failure invisibili a un revisore umano
                      (micro-paraphrase + truncation). Metrica di RISCHIO.
    - depth_profile:  pass-rate per terzo del documento (early/mid/late).
                      Smaschera il "lost in the middle".
    """
    total_anchors = 0
    verbatim_anchors = 0
    by_target_kind = {}
    failure_modes = {}
    examples = {}
    ratio_sum = 0.0
    damage = 0
    severity = 0
    silent_count = 0
    fail_count = 0
    depth = {"early": [0, 0], "mid": [0, 0], "late": [0, 0]}

    for case in scored["cases"]:
        targets_by_id = case.get("targets_by_id") or {}
        for q_idx, q in enumerate(case["quotes"], 1):
            total_anchors += 1
            tk = q.get("target_kind", "field")
            by_target_kind.setdefault(tk, {"n": 0, "ok": 0})
            by_target_kind[tk]["n"] += 1
            ratio_sum += q.get("best_ratio") or 0
            damage += q.get("invented_chars") or 0

            # Bucket per profondita' nel documento (in base al numero domanda).
            target = targets_by_id.get(str(q_idx)) or {}
            num = target.get("numero")
            if isinstance(num, int) and 1 <= num <= 297:
                bucket = "early" if num < 100 else ("mid" if num < 200 else "late")
                depth[bucket][1] += 1
                if q["verbatim"]:
                    depth[bucket][0] += 1

            if q["verbatim"]:
                verbatim_anchors += 1
                by_target_kind[tk]["ok"] += 1
            else:
                fm = q.get("failure_mode") or "other"
                failure_modes[fm] = failure_modes.get(fm, 0) + 1
                examples.setdefault(fm, (case["case_id"], q_idx))
                fail_count += 1
                severity += SEVERITY_WEIGHTS.get(fm, 5)
                if fm in SILENT_MODES:
                    silent_count += 1

    soft = (ratio_sum / total_anchors) if total_anchors else 0.0
    silent_rate = (silent_count / fail_count * 100) if fail_count else 0.0

    return {
        "total_anchors":  total_anchors,
        "verbatim_anchors": verbatim_anchors,
        "verbatim_rate":  (verbatim_anchors / total_anchors * 100) if total_anchors else 0.0,
        "soft_fidelity":  soft,
        "damage":         damage,
        "severity":       severity,
        "silent_count":   silent_count,
        "silent_rate":    silent_rate,
        "fail_count":     fail_count,
        "depth_profile":  depth,
        "by_target_kind": by_target_kind,
        "failure_modes":  failure_modes,
        "failure_examples": examples,
        "invented_chars_total": damage,  # alias retro-compat
    }


def _model_stem(model_id: str) -> str:
    """Estrae la 'linea' del modello rimuovendo i numeri di versione.

    Serve per distinguere 'stessa linea, versione diversa' (es. gpt-4.1 vs
    gpt-5.4) da 'tier diverso stessa generazione' (es. sonnet-4.6 vs opus-4.6).
    """
    import re
    n = model_id.split("/")[-1]
    stem = re.sub(r"-v?\d+(\.\d+)?", "", n)
    stem = re.sub(r"-+", "-", stem).strip("-")
    return stem


def _pretty_label(stem: str) -> str:
    """Capitalizzazione human-friendly per sigle note (GPT, ecc.)."""
    parts = stem.split("-")
    nice = []
    for p in parts:
        if p.lower() in ("gpt", "kvn", "llm", "api"):
            nice.append(p.upper())
        else:
            nice.append(p.capitalize())
    return " ".join(nice)


def compute_executive_summary(models: list[dict]) -> list[str]:
    """Character portraits — Scenario C.

    Una riga = un ritratto. Pochi numeri, molta caratterizzazione.
    L'idea: il lettore in 30 secondi conosce i 'personaggi' del round."""
    portraits = []
    if not models:
        return portraits

    from collections import defaultdict
    by_fam = defaultdict(list)
    for m in models:
        by_fam[m["model"].split("/")[0]].append(m)

    median_hard = sorted(m["agg"]["verbatim_rate"] for m in models)[len(models)//2]

    # 1) Lo scriba imbattuto — modelli perfetti
    perfect = [m for m in models if m["agg"]["verbatim_rate"] >= 100.0]
    if perfect:
        fams = {m["model"].split("/")[0] for m in perfect}
        if len(fams) == 1 and len(perfect) >= 2:
            fam = list(fams)[0].title()
            portraits.append(
                f"<b>{fam}'s Pro family — the unrivaled scribe.</b> "
                f"Both variants read the document and return it untouched. "
                f"They are the only models in the panel to reach a perfect score."
            )
        else:
            names = " and ".join(m["model"].split("/")[-1] for m in perfect)
            portraits.append(
                f"<b>{names} — the perfect scribes.</b> "
                f"They reproduce every anchor without a comma out of place."
            )

    # 2) Famiglia con failure pattern identico — scale doesn't rescue
    for fam, ms in by_fam.items():
        if len(ms) < 2:
            continue
        scores = [m["agg"]["verbatim_rate"] for m in ms]
        if all(s < median_hard for s in scores) and (max(scores) - min(scores)) < 5:
            names = " and ".join(m["model"].split("/")[-1] for m in ms)
            portraits.append(
                f"<b>{fam.title()} — built for tools, not transcription.</b> "
                f"{names} fail in the same way and at the same rate. "
                f"The bigger model does not escape the pattern: this looks like a "
                f"family-wide specialization, not a parameter problem."
            )
            break

    # 3) Il bargain
    valued = [(m["agg"]["verbatim_rate"] / max(m["meta"]["cost_usd"], 0.001), m)
              for m in models if m["meta"]["cost_usd"] > 0 and m["agg"]["verbatim_rate"] > 50]
    valued.sort(key=lambda t: -t[0])
    if valued:
        _, best = valued[0]
        nm = best["model"].split("/")[-1]
        portraits.append(
            f"<b>{nm} — the bargain that thinks.</b> "
            f"It does its scaffolding internally rather than calling tools, "
            f"and lands near the top of the leaderboard for a fraction of what "
            f"the premium models charge. (Latency in this run mixes model "
            f"behavior with the third-party provider that OpenRouter routed "
            f"the request to — read it with caution.)"
        )

    # Per "stable / generational leap" raggruppa per LINEA (stesso stem
    # senza numero di versione), non per famiglia: cosi' sonnet-4.6 e
    # opus-4.6 sono separati (tier diversi), mentre gpt-4.1 e gpt-5.4
    # finiscono insieme (stessa linea, generazioni diverse).
    by_line = defaultdict(list)
    for m in models:
        by_line[_model_stem(m["model"])].append(m)

    # 4) Stabilita' tra generazioni — stesso punteggio, costo crescente
    stable_added = False
    for line, ms in by_line.items():
        if len(ms) < 2:
            continue
        scores = sorted([(m["agg"]["verbatim_rate"], m) for m in ms], key=lambda t: -t[0])
        if scores[0][0] - scores[-1][0] < 1.0:
            costs = [m["meta"]["cost_usd"] for m in ms]
            if max(costs) > min(costs) * 1.2 and 50 < scores[0][0] < 100:
                names = " and ".join(m["model"].split("/")[-1] for m in ms)
                portraits.append(
                    f"<b>{_pretty_label(line)} line — stable across generations.</b> "
                    f"{names} land at the same score; the newer one just costs more "
                    f"and runs slower. No discernible improvement on this axis."
                )
                stable_added = True
                break

    # 5) Salto generazionale drammatico (es. Flash 2.5 vs 3.5)
    if not stable_added:
        for line, ms in by_line.items():
            if len(ms) < 2:
                continue
            scores = sorted([(m["agg"]["verbatim_rate"], m) for m in ms])
            jump = scores[-1][0] - scores[0][0]
            if jump >= 20 and scores[-1][0] < 100:
                low_nm = scores[0][1]["model"].split("/")[-1]
                high_nm = scores[-1][1]["model"].split("/")[-1]
                portraits.append(
                    f"<b>{high_nm} — the generational leap.</b> "
                    f"Where its sibling {low_nm} stumbles, the newer release "
                    f"recovers most of the gap. Proof the problem is tractable "
                    f"when training prioritizes it."
                )
                break

    return portraits[:4]


def compute_findings(models: list[dict]) -> list[str]:
    """Produce 3-5 affermazioni narrative dai dati, in stile 'inchiesta'."""
    findings = []
    if not models:
        return findings

    # 1) chi e' perfetto
    perfect = [m for m in models if m["agg"]["verbatim_rate"] == 100.0]
    if perfect:
        names = ", ".join(m["model"].split("/")[-1] for m in perfect)
        findings.append(
            f"<b>Only {len(perfect)} of {len(models)} models reproduce the document without error.</b> "
            f"The flawless set: {names}."
        )

    # 2) tie clusters: stesso hard score tra 2+ modelli
    from collections import defaultdict
    by_hard = defaultdict(list)
    for m in models:
        by_hard[round(m["agg"]["verbatim_rate"], 1)].append(m)
    ties = [(s, ms) for s, ms in by_hard.items() if len(ms) > 1 and s < 100.0]
    ties.sort(key=lambda t: -t[0])
    for score, ms in ties[:1]:
        names = ", ".join(m["model"].split("/")[-1] for m in ms)
        # spread interno su severity
        sevs = sorted((m["agg"]["severity"], m["model"].split("/")[-1]) for m in ms)
        if sevs[0][0] != sevs[-1][0]:
            findings.append(
                f"<b>A {len(ms)}-way tie at {score:.1f} hides three personalities.</b> "
                f"{names} agree on the headline number but disagree on severity: "
                f"{sevs[0][1]} ({sevs[0][0]} weighted), {sevs[-1][1]} ({sevs[-1][0]} weighted)."
            )

    # 3) family pattern (provider)
    by_prov = defaultdict(list)
    for m in models:
        prov = m["model"].split("/")[0]
        by_prov[prov].append(m)
    median_hard = sorted(m["agg"]["verbatim_rate"] for m in models)[len(models)//2]
    for prov, ms in by_prov.items():
        if len(ms) >= 2 and all(m["agg"]["verbatim_rate"] < median_hard for m in ms):
            findings.append(
                f"<b>The {prov} family lags on this task.</b> "
                f"All {len(ms)} {prov} models score below the median hard rate ({median_hard:.0f}). "
                f"Not a parameter-size problem: it is a family-wide pattern."
            )

    # 4) miglior rapporto qualita/prezzo
    scored = [(m["agg"]["verbatim_rate"] / max(m["meta"]["cost_usd"], 0.001), m) for m in models if m["meta"]["cost_usd"] > 0]
    scored.sort(key=lambda t: -t[0])
    if scored:
        ratio, best = scored[0]
        cost = best["meta"]["cost_usd"]
        score = best["agg"]["verbatim_rate"]
        findings.append(
            f"<b>{best['model'].split('/')[-1]} dominates on value.</b> "
            f"Score {score:.1f} at ${cost:.3f}: {ratio:,.0f} points per dollar, "
            f"orders of magnitude ahead of premium competitors."
        )

    # 5) il piu' "silenzioso" (rischio nascosto) tra i top
    risky = [m for m in models if m["agg"]["fail_count"] > 0 and m["agg"]["verbatim_rate"] >= 90]
    risky.sort(key=lambda m: -m["agg"]["silent_rate"])
    if risky:
        r = risky[0]
        sr = r["agg"]["silent_rate"]
        if sr >= 50:
            findings.append(
                f"<b>The silent paradox: {r['model'].split('/')[-1]} looks great until you look closely.</b> "
                f"Its hard score of {r['agg']['verbatim_rate']:.1f} hides a {sr:.0f}% silent failure rate — "
                f"the kind of failure a human reviewer would not catch by eye "
                f"({r['agg']['silent_count']} silent failure"
                f"{'' if r['agg']['silent_count'] == 1 else 's'} / "
                f"{r['agg']['fail_count']} total failure"
                f"{'' if r['agg']['fail_count'] == 1 else 's'} / "
                f"{r['agg']['total_anchors']} anchors)."
            )

    return findings


def aggregate_run_meta(scored_raw: dict) -> dict:
    """Tira fuori costo totale, latenze, cache info dalla parte 'responses' del run."""
    cost = 0.0
    latencies = []
    cache_reads = 0
    cache_writes = 0
    for r in scored_raw.get("responses", []):
        u = r.get("usage") or {}
        cost += u.get("cost") or 0.0
        if r.get("latency_s") is not None:
            latencies.append(r["latency_s"])
        pd = u.get("prompt_tokens_details") or {}
        if isinstance(pd, dict):
            cache_reads += pd.get("cached_tokens") or 0
            cache_writes += pd.get("cache_write_tokens") or 0
        cache_reads += u.get("cache_read_input_tokens") or 0
        cache_writes += u.get("cache_creation_input_tokens") or 0
    return {
        "cost_usd": cost,
        "latency_total_s": sum(latencies),
        "latency_median_s": statistics.median(latencies) if latencies else 0,
        "cache_reads": cache_reads,
        "cache_writes": cache_writes,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

# URL pubblico del report (per Open Graph / Twitter Card).
# Override via flag --site-url quando si forka.
DEFAULT_SITE_URL = "https://scarsellifi.github.io/Klaatu-Verata-Nikto-Bench-"


METRICS_SPEC = [
    {"key":"hard",     "label":"Hard score",      "unit":"/100", "higher_better":True,  "fmt":".1f", "field":"verbatim_rate", "src":"agg",
     "lede":"Strict verbatim rate. Pass = exact match, character by character."},
    {"key":"soft",     "label":"Soft fidelity",   "unit":"/100", "higher_better":True,  "fmt":".1f", "field":"soft_fidelity", "src":"agg",
     "lede":"Average char similarity across all anchors. Rewards models whose failures are <i>close</i> to the source."},
    {"key":"damage",   "label":"Damage",          "unit":"chr",  "higher_better":False, "fmt":"d",   "field":"damage",        "src":"agg",
     "lede":"Invented characters in total. How much text was produced that does not appear in the source."},
    {"key":"severity", "label":"Severity",        "unit":"",     "higher_better":False, "fmt":"d",   "field":"severity",      "src":"agg",
     "lede":"Weighted failure gravity. Micro-paraphrase = 1, hallucination = 10. Few-but-bad versus many-but-minor."},
    {"key":"silent",   "label":"Silent failure rate", "unit":"%","higher_better":False, "fmt":".0f", "field":"silent_rate",   "src":"agg",
     "lede":"Share of failures invisible to a human reviewer. Read alongside silent failures / total failures / total anchors. <b>The risk metric for unmonitored pipelines.</b>"},
    {"key":"cost",     "label":"Audit cost",      "unit":"$",    "higher_better":False, "fmt":".3f", "field":"cost_usd",      "src":"meta",
     "lede":"Total spend for one run on this document, with caching enabled where available."},
]


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Klaatu Verata Nikto — A Literal Extraction Audit</title>
<meta name="description" content="A benchmark for LLMs that copy without inventing. One task — verbatim extraction from a long document — measured across 9 frontier models, with auto-derived findings and per-failure char-level diffs.">

<!-- Open Graph / Facebook / LinkedIn -->
<meta property="og:type" content="article">
<meta property="og:title" content="Klaatu Verata Nikto — A Literal Extraction Audit">
<meta property="og:description" content="Can frontier LLMs still copy a passage verbatim from a long document, with no tools? A benchmark across 9 models. Auto-derived findings, char-level diffs.">
<meta property="og:image" content="__SITE_URL__/tools.png">
<meta property="og:url" content="__SITE_URL__/">
<meta property="og:site_name" content="Klaatu Verata Nikto Bench">

<!-- Twitter / X -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Klaatu Verata Nikto — A Literal Extraction Audit">
<meta name="twitter:description" content="A benchmark for LLMs that copy without inventing. 9 frontier models, one long document, no tools.">
<meta name="twitter:image" content="__SITE_URL__/tools.png">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
  :root {
    --paper:      #f1e8d5;
    --paper-2:    #e8ddc5;
    --paper-edge: #d4c7a5;
    --ink:        #1c1612;
    --ink-soft:   #423630;
    --ink-faint:  #6e5c4f;
    --rule:       #b8a884;
    --pass:       #2e5c2e;
    --pass-soft:  #d8e2cb;
    --fail:       #8a1d1d;
    --fail-soft:  #ecd0ce;
    --warn:       #a87520;
    --warn-soft:  #e8d8b6;
    --accent:     #5a3a26;
    --serif-display: 'Cormorant Garamond', Georgia, serif;
    --serif-body:    'EB Garamond', 'Georgia', serif;
    --mono:          'JetBrains Mono', 'Menlo', monospace;
  }
  *  { box-sizing: border-box; }
  html, body { background: var(--paper); color: var(--ink); margin: 0; }
  body {
    font-family: var(--serif-body);
    font-size: 17px;
    line-height: 1.55;
    background-image:
      radial-gradient(rgba(80,60,30,0.045) 1px, transparent 1px),
      radial-gradient(rgba(80,60,30,0.03)  1px, transparent 1px);
    background-size: 3px 3px, 7px 7px;
    background-position: 0 0, 1px 1px;
  }
  ::selection { background: var(--ink); color: var(--paper); }

  .page { max-width: 1180px; margin: 0 auto; padding: 56px 48px 96px; }

  /* ─── Masthead ─── */
  .masthead {
    text-align: center;
    border-top: 3px double var(--ink);
    border-bottom: 3px double var(--ink);
    padding: 22px 0 26px;
    margin-bottom: 36px;
    position: relative;
  }
  .masthead .kicker {
    font-family: var(--mono); font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.35em; color: var(--ink-faint);
    margin-bottom: 8px;
  }
  .masthead h1 {
    font-family: var(--serif-display); font-weight: 600;
    font-size: clamp(44px, 7vw, 76px);
    line-height: 0.95; letter-spacing: -0.01em;
    margin: 0 0 6px 0;
  }
  .masthead .subtitle {
    font-style: italic; color: var(--ink-soft); font-size: 19px;
    margin-bottom: 14px;
  }
  .masthead .meta {
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    text-transform: uppercase; letter-spacing: 0.18em;
  }
  .masthead .meta b { color: var(--ink); }
  .ornament { color: var(--ink-faint); font-size: 18px; margin: 0 10px; }

  /* ─── Hero stats strip ─── */
  .hero-stats {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin: 0 0 44px 0;
    border: 1px solid var(--rule);
    background: var(--paper-2);
  }
  .hero-stats .cell {
    padding: 18px 22px; border-right: 1px solid var(--rule);
    text-align: center;
  }
  .hero-stats .cell:last-child { border-right: none; }
  .hero-stats .label {
    font-family: var(--mono); font-size: 10px; letter-spacing: 0.2em;
    text-transform: uppercase; color: var(--ink-faint);
  }
  .hero-stats .value {
    font-family: var(--serif-display); font-size: 38px; font-weight: 600;
    color: var(--ink); line-height: 1; margin: 6px 0 0;
  }
  .hero-stats .unit {
    font-size: 14px; color: var(--ink-faint); font-family: var(--serif-body);
    font-style: italic; margin-left: 2px;
  }

  /* ─── Section headers ─── */
  .section {
    margin: 56px 0 28px;
    display: flex; align-items: baseline; gap: 16px;
  }
  .section .num {
    font-family: var(--serif-display); font-size: 56px; font-weight: 500;
    color: var(--paper-edge); line-height: 0.8;
  }
  .section h2 {
    font-family: var(--serif-display); font-weight: 500;
    font-size: 34px; margin: 0; letter-spacing: -0.01em;
  }
  .section .lede {
    color: var(--ink-soft); font-style: italic;
    border-left: 2px solid var(--rule); padding-left: 14px; margin-left: auto;
    max-width: 540px; font-size: 15px; line-height: 1.5;
  }

  /* ─── Legend block ─── */
  .legend { background: var(--paper-2); border: 1px solid var(--rule);
            padding: 22px 26px; margin-bottom: 32px; }
  .legend summary {
    cursor: pointer; font-family: var(--serif-display); font-size: 20px;
    font-weight: 600; color: var(--ink);
    list-style: none;
  }
  .legend summary::after { content: " ✦"; color: var(--ink-faint); }
  .legend[open] summary::after { content: " ✦"; color: var(--ink); }
  .legend-body { margin-top: 14px; column-count: 2; column-gap: 36px;
                 font-size: 15px; color: var(--ink-soft); }
  .legend-body p { margin: 8px 0; break-inside: avoid; }
  .legend-body b { color: var(--ink); }
  .legend-body code { font-family: var(--mono); font-size: 13px;
                      background: var(--paper); padding: 1px 6px;
                      border: 1px solid var(--rule); }
  .legend-body ul { margin: 4px 0 10px 18px; padding: 0; }
  .legend-body li { margin: 3px 0; break-inside: avoid; }
  .validity {
    margin: 22px 0 34px;
    padding: 20px 24px 22px;
    background: var(--paper);
    border: 1px solid var(--rule);
  }
  .validity h3 {
    margin: 0 0 8px;
    font-family: var(--serif-display);
    font-size: 26px;
    font-weight: 600;
  }
  .validity p {
    margin: 0 0 10px;
    color: var(--ink-soft);
  }
  .validity ul { margin: 0 0 0 18px; padding: 0; }
  .validity li { margin: 4px 0; color: var(--ink-soft); }
  .validity b { color: var(--ink); }
  .hypothesis {
    margin: 22px 0 34px;
    padding: 24px 28px 26px;
    background: linear-gradient(180deg, rgba(90,58,38,0.06), rgba(90,58,38,0.02));
    border: 1px solid var(--rule);
    border-left: 5px solid var(--accent);
  }
  .hypothesis .kicker {
    margin: 0 0 8px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--ink-faint);
  }
  .hypothesis h3 {
    margin: 0 0 8px;
    font-family: var(--serif-display);
    font-size: 30px;
    font-weight: 600;
  }
  .hypothesis p {
    margin: 0 0 10px;
    color: var(--ink-soft);
  }
  .hypothesis ul { margin: 10px 0 0 18px; padding: 0; }
  .hypothesis li { margin: 6px 0; color: var(--ink-soft); }
  .hypothesis b { color: var(--ink); }
  .hypothesis .note {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed var(--rule);
    font-style: italic;
  }
  .hypothesis-image {
    display: block; margin: 6px auto 18px;
    max-width: 380px; width: 100%;
    border: 1px solid var(--ink); padding: 6px;
    background: var(--paper);
    box-shadow: 0 1px 0 var(--rule);
  }

  /* ─── Responsive (mobile / narrow) ─── */
  @media (max-width: 720px) {
    body { font-size: 16px; }
    .page { padding: 28px 16px 56px; }
    .masthead { padding: 16px 0 20px; }
    .masthead h1 { font-size: 38px; line-height: 1; }
    .masthead .subtitle { font-size: 15px; padding: 0 4px; }
    .masthead .meta { font-size: 9px; line-height: 1.8; letter-spacing: 0.12em; }
    .masthead .ornament { display: none; }
    .hero-image { width: 72px; height: 72px; }

    /* Challenge: less padding, smaller heading */
    .challenge { padding: 18px 18px 16px; border-left-width: 4px; }
    .challenge .ch-kicker { font-size: 21px; }
    .challenge p { font-size: 15px; }

    /* Hero stats: 2x2 grid instead of 1x4 */
    .hero-stats { grid-template-columns: repeat(2, 1fr); }
    .hero-stats .cell { padding: 14px 12px; border-right: 1px solid var(--rule); }
    .hero-stats .cell:nth-child(2n) { border-right: none; }
    .hero-stats .cell:nth-child(-n+2) { border-bottom: 1px solid var(--rule); }
    .hero-stats .value { font-size: 28px; }

    /* Section headers: stack lede below */
    .section { flex-direction: column; align-items: flex-start; gap: 4px; }
    .section .num { font-size: 36px; line-height: 1; }
    .section h2 { font-size: 22px; }
    .section .lede {
      margin-left: 0; border-left: 2px solid var(--rule); padding-left: 12px;
      max-width: none; font-size: 14px;
    }

    /* Exec summary: less padding, smaller numbers */
    .exec-summary { padding: 22px 18px 24px; }
    .exec-summary li {
      grid-template-columns: 38px 1fr; gap: 12px;
      font-size: 16px; line-height: 1.4;
    }

    /* Legend: collapse the 2-column reading layout */
    .legend { padding: 16px 18px; }
    .legend-body { column-count: 1; font-size: 14px; }

    /* Rankings: 1 column, drop the visual bar (keep number+value) */
    .rankings { grid-template-columns: 1fr; gap: 16px; }
    .ranking { padding: 16px 18px; }
    .ranking h3 { font-size: 18px; flex-wrap: wrap; }
    .ranking h3 .arrow { font-size: 9px; }
    .ranking li {
      grid-template-columns: 20px 1fr 70px;
      gap: 8px; font-size: 12px;
    }
    .ranking li .bar { display: none; }
    .ranking li .nm { font-size: 10px; }

    /* Maps: SVG already responsive; just stack the cards */
    .maps { grid-template-columns: 1fr; gap: 16px; }
    .map { padding: 16px 18px; }
    .map svg { height: 280px; }

    /* Taxonomy: 1 column */
    .taxonomy { grid-template-columns: 1fr; gap: 14px; }

    /* Specimen detail */
    .specimen { padding: 14px 16px; }
    .specimen summary { gap: 8px; }
    .specimen .summary-text { font-size: 14px; min-width: 0; }
    .specimen .open-indicator { display: none; }
    .specimen-body .anchor-info { font-size: 10px; }
    .diff { font-size: 11px; padding: 10px 12px; }

    /* Hypothesis: smaller image, less padding */
    .hypothesis { padding: 18px 20px; border-left-width: 4px; }
    .hypothesis h3 { font-size: 22px; }
    .hypothesis-image { max-width: 280px; }

    /* Validity / findings */
    .validity, .findings { padding: 16px 18px; }
    .validity h3 { font-size: 20px; }
    .finding { padding: 14px 18px; font-size: 15px; }
    .finding .num-pill { font-size: 18px; margin-right: 8px; }
  }

  /* ─── Model cards ─── */
  .models-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
    gap: 20px;
  }
  .model {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 22px 24px 18px; position: relative;
  }
  .model .rank {
    position: absolute; top: -12px; left: 18px;
    background: var(--ink); color: var(--paper);
    width: 28px; height: 28px; display: flex;
    align-items: center; justify-content: center;
    font-family: var(--serif-display); font-weight: 600; font-size: 16px;
  }
  .model .name {
    font-family: var(--mono); font-size: 12px; color: var(--ink-soft);
    word-break: break-all; margin-bottom: 6px;
    border-bottom: 1px dashed var(--rule); padding-bottom: 8px;
  }
  .scores-row { display: flex; align-items: flex-end; gap: 22px;
                margin: 14px 0 6px; padding-bottom: 12px;
                border-bottom: 1px dashed var(--rule); }
  .score-main { display: flex; flex-direction: column; }
  .score-main .big {
    font-family: var(--serif-display); font-weight: 600;
    line-height: 0.85; letter-spacing: -0.02em;
    font-size: 56px;
  }
  .score-main .big.secondary { font-size: 38px; opacity: 0.88; }
  .score-main .suf { font-size: 16px; color: var(--ink-faint); margin-left: 3px; }
  .score-main .lab {
    font-family: var(--mono); font-size: 9px; letter-spacing: 0.22em;
    text-transform: uppercase; color: var(--ink-faint); margin-top: 6px;
  }
  .score-main.good .big { color: var(--pass); }
  .score-main.mid  .big { color: var(--warn); }
  .score-main.bad  .big { color: var(--fail); }
  .model .verbatim { color: var(--ink-soft); font-style: italic;
                     font-size: 14px; margin-bottom: 4px; }
  .bars-label {
    font-family: var(--mono); font-size: 9px; letter-spacing: 0.2em;
    text-transform: uppercase; color: var(--ink-faint);
    margin: 14px 0 6px;
  }
  .metric-pill {
    display: inline-block; padding: 1px 7px; font-size: 10px;
    font-family: var(--mono); letter-spacing: 0.08em;
    border: 1px solid var(--rule); background: var(--paper);
    margin-left: 6px; vertical-align: middle;
  }
  .metric-pill.good { background: var(--pass-soft); border-color: var(--pass); color: var(--pass); }
  .metric-pill.mid  { background: var(--warn-soft); border-color: var(--warn); color: var(--ink); }
  .metric-pill.bad  { background: var(--fail-soft); border-color: var(--fail); color: var(--fail); font-weight: 700; }

  .kind-bars { display: flex; flex-direction: column; gap: 7px;
               margin: 14px 0 16px; }
  .kind-row { display: grid; grid-template-columns: 90px 1fr 44px;
              align-items: center; gap: 10px; font-size: 13px; }
  .kind-row .k { font-family: var(--mono); font-size: 10px;
                 text-transform: uppercase; letter-spacing: 0.15em;
                 color: var(--ink-faint); }
  .kind-row .bar { height: 6px; background: var(--paper-edge);
                   position: relative; overflow: hidden; }
  .kind-row .bar-fill { position: absolute; top: 0; left: 0;
                        height: 100%; background: var(--ink); }
  .kind-row .bar-fill.good { background: var(--pass); }
  .kind-row .bar-fill.mid  { background: var(--warn); }
  .kind-row .bar-fill.bad  { background: var(--fail); }
  .kind-row .n  { font-family: var(--mono); font-size: 11px; text-align: right;
                  color: var(--ink-soft); }

  .model-meta { display: grid; grid-template-columns: 1fr 1fr;
                gap: 10px 18px; font-size: 12px;
                padding-top: 14px; border-top: 1px dashed var(--rule); }
  .model-meta .pair { display: flex; flex-direction: column; }
  .model-meta .pair .k {
    font-family: var(--mono); font-size: 9px; letter-spacing: 0.2em;
    text-transform: uppercase; color: var(--ink-faint);
  }
  .model-meta .pair .v { font-size: 14px; color: var(--ink); margin-top: 2px;
                         font-family: var(--serif-body); }
  .model-meta .pair .v.mono { font-family: var(--mono); font-size: 12px; }

  .insight {
    margin-top: 12px; padding-top: 12px;
    border-top: 1px dashed var(--rule);
    font-size: 13px; font-style: italic; color: var(--ink-soft);
  }
  .insight b { font-style: normal; color: var(--ink); }

  /* ─── Difficulty heatmap ─── */
  .heatmap {
    background: var(--paper-2); border: 1px solid var(--rule);
    overflow-x: auto;
  }
  .heatmap table { width: 100%; border-collapse: collapse;
                   font-family: var(--mono); font-size: 11px; }
  .heatmap th, .heatmap td {
    padding: 6px 8px; border-bottom: 1px solid var(--paper-edge);
    text-align: center; white-space: nowrap;
  }
  .heatmap th { background: var(--paper);
                font-weight: 500; font-size: 10px; letter-spacing: 0.15em;
                text-transform: uppercase; color: var(--ink-faint);
                position: sticky; top: 0; }
  .heatmap td.id { text-align: left; color: var(--ink-soft); width: 40px; }
  .heatmap td.target { text-align: left; color: var(--ink-soft);
                       max-width: 220px; overflow: hidden;
                       text-overflow: ellipsis;
                       font-family: var(--serif-body); font-size: 13px;
                       font-style: italic; }
  .heatmap td.kind { font-size: 9px; color: var(--ink-faint);
                     letter-spacing: 0.1em; }
  .heatmap td.diff-col { text-align: center; }
  .heatmap td.cell { font-size: 14px; }
  .heatmap td.cell.pass { color: var(--pass); }
  .heatmap td.cell.fail { color: var(--fail); font-weight: bold; }
  .heatmap td.cell.miss { color: var(--ink-faint); }
  .heatmap tr.hard { background: rgba(168,29,29,0.04); }
  .heatmap tr.hard td.id::before { content: "⚠ "; color: var(--fail); }

  /* ─── Failure taxonomy ─── */
  .taxonomy { display: grid;
              grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
              gap: 18px; margin-top: 8px; }
  .taxo-card {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 16px 18px;
  }
  .taxo-card .name { font-family: var(--serif-display); font-size: 22px;
                     font-weight: 600; margin: 0 0 4px; }
  .taxo-card .desc { font-size: 13px; color: var(--ink-soft);
                     font-style: italic; line-height: 1.45;
                     margin-bottom: 12px; min-height: 56px; }
  .taxo-card .counts { display: flex; flex-direction: column; gap: 3px;
                       padding-top: 10px; border-top: 1px dashed var(--rule);
                       font-family: var(--mono); font-size: 11px; }
  .taxo-card .counts .row { display: flex; justify-content: space-between; }
  .taxo-card .counts .row .m { color: var(--ink-soft); }
  .taxo-card .counts .row .n { color: var(--ink); font-weight: 700; }
  .taxo-card .total {
    font-family: var(--serif-display); font-size: 44px; font-weight: 600;
    line-height: 0.9; color: var(--ink); margin-bottom: 2px;
  }
  .taxo-card .total .unit { font-size: 13px; color: var(--ink-faint);
                            font-family: var(--serif-body); font-style: italic;
                            margin-left: 4px; }

  /* ─── Specimens (per-failure detail) ─── */
  .specimen {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 18px 22px; margin-bottom: 16px;
  }
  .specimen summary { cursor: pointer; list-style: none;
                      display: flex; align-items: center; gap: 14px;
                      flex-wrap: wrap; }
  .specimen summary::-webkit-details-marker { display: none; }
  .specimen .tag {
    font-family: var(--mono); font-size: 10px; letter-spacing: 0.15em;
    text-transform: uppercase; padding: 3px 8px;
    border: 1px solid var(--ink); background: var(--paper);
  }
  .specimen .tag.fail { background: var(--fail); color: var(--paper);
                        border-color: var(--fail); }
  .specimen .tag.mode {
    background: var(--warn-soft); border-color: var(--warn); color: var(--ink);
  }
  .specimen .model-name {
    font-family: var(--mono); font-size: 12px; color: var(--ink-soft);
  }
  .specimen .summary-text {
    font-family: var(--serif-body); font-size: 15px; color: var(--ink);
    flex: 1; min-width: 200px;
  }
  .specimen .open-indicator {
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    margin-left: auto;
  }
  .specimen[open] .open-indicator::after { content: " (close)"; }
  .specimen:not([open]) .open-indicator::after { content: " (expand →)"; }

  .specimen-body { margin-top: 18px; padding-top: 16px;
                   border-top: 1px dashed var(--rule); }
  .specimen-body .why {
    background: var(--paper); border-left: 3px solid var(--warn);
    padding: 10px 14px; margin-bottom: 14px;
    font-style: italic; color: var(--ink-soft); font-size: 14px;
  }
  .specimen-body .anchor-info {
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    margin-bottom: 12px; padding-bottom: 10px;
    border-bottom: 1px dashed var(--rule);
  }
  .specimen-body .anchor-info b { color: var(--ink); }
  .diff-legend {
    font-family: var(--mono); font-size: 10px; color: var(--ink-faint);
    display: flex; gap: 18px; margin: 12px 0 8px;
    text-transform: uppercase; letter-spacing: 0.1em;
  }
  .diff-legend .sw del, .diff-legend .sw ins {
    padding: 0 4px; font-family: var(--mono);
  }
  .diff {
    background: var(--paper); padding: 14px 16px; border: 1px solid var(--rule);
    font-family: var(--mono); font-size: 13px; line-height: 1.65;
    white-space: pre-wrap; word-break: break-word;
    max-height: 320px; overflow-y: auto;
  }
  .diff .eq { color: var(--ink); }
  .diff del {
    background: var(--fail-soft); color: var(--fail);
    text-decoration: line-through;
    text-decoration-color: rgba(138,29,29,0.6);
  }
  .diff ins {
    background: var(--pass-soft); color: var(--pass);
    text-decoration: none; font-weight: 600;
  }

  /* ─── The challenge (intro) ─── */
  .challenge {
    background: var(--paper); border: 1px solid var(--rule);
    padding: 26px 30px 24px; margin-bottom: 28px;
    border-left: 5px solid var(--ink);
  }
  .challenge .ch-kicker {
    font-family: var(--serif-display); font-size: 26px; font-weight: 600;
    margin-bottom: 12px;
  }
  .challenge p {
    margin: 0 0 10px; font-size: 17px; line-height: 1.55;
    color: var(--ink-soft); max-width: 78ch;
  }
  .challenge p:last-child { margin-bottom: 0; }
  .challenge b { color: var(--ink); font-weight: 600; }
  .challenge code {
    font-family: var(--mono); font-size: 14px;
    background: var(--paper-2); padding: 1px 6px; border: 1px solid var(--rule);
  }

  /* ─── Executive summary ─── */
  .exec-summary {
    background: var(--ink); color: var(--paper);
    padding: 28px 32px 30px; margin-bottom: 36px;
    border-radius: 0; position: relative;
  }
  .exec-summary::before {
    content: ""; position: absolute; top: -8px; left: 0; right: 0;
    height: 8px; background: repeating-linear-gradient(
      90deg, var(--ink) 0 8px, var(--paper) 8px 12px);
  }
  .exec-title {
    font-family: var(--mono); font-size: 10px; letter-spacing: 0.35em;
    text-transform: uppercase; color: var(--paper-edge); margin-bottom: 16px;
  }
  .exec-summary ol {
    margin: 0; padding-left: 0; list-style: none;
    counter-reset: exec;
  }
  .exec-summary li {
    counter-increment: exec;
    font-family: var(--serif-display); font-size: 22px; line-height: 1.35;
    font-weight: 500; padding: 12px 0;
    border-bottom: 1px solid rgba(241,232,213,0.15);
    display: grid; grid-template-columns: 56px 1fr; gap: 18px;
    align-items: baseline;
  }
  .exec-summary li:last-child { border-bottom: none; }
  .exec-summary li::before {
    content: counter(exec, decimal-leading-zero);
    font-family: var(--mono); font-size: 12px; letter-spacing: 0.2em;
    color: var(--paper-edge); padding-top: 6px;
  }
  .exec-summary b { color: var(--warn-soft); font-weight: 600; }

  /* ─── Findings (narrative) ─── */
  .findings { display: grid; gap: 14px; }
  .finding {
    background: var(--paper-2); border-left: 4px solid var(--ink);
    padding: 18px 24px;
    font-family: var(--serif-body); font-size: 17px; line-height: 1.6;
    color: var(--ink-soft);
  }
  .finding b { color: var(--ink); font-weight: 600; }
  .finding .num-pill {
    display: inline-block; font-family: var(--serif-display);
    font-size: 22px; font-weight: 600; color: var(--ink-faint);
    margin-right: 12px;
  }

  /* ─── Rankings ─── */
  .rankings {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
    gap: 22px;
  }
  .ranking {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 20px 22px;
  }
  .ranking h3 {
    font-family: var(--serif-display); font-size: 22px; font-weight: 600;
    margin: 0 0 4px;
    display: flex; align-items: baseline; gap: 10px;
  }
  .ranking h3 .arrow {
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    letter-spacing: 0.12em; text-transform: uppercase;
  }
  .ranking .ld {
    font-style: italic; color: var(--ink-soft); font-size: 13px;
    margin-bottom: 14px; line-height: 1.45;
  }
  .ranking ol {
    list-style: none; padding: 0; margin: 0;
    display: flex; flex-direction: column; gap: 6px;
  }
  .ranking li {
    display: grid; grid-template-columns: 22px 130px 1fr 80px;
    align-items: center; gap: 10px;
    font-size: 13px;
  }
  .ranking li .rk {
    font-family: var(--serif-display); font-weight: 600;
    color: var(--ink-faint); text-align: right;
  }
  .ranking li.top .rk { color: var(--accent); font-size: 18px; }
  .ranking li .nm {
    font-family: var(--mono); font-size: 11px;
    color: var(--ink); overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }
  .ranking li .bar {
    height: 10px; background: var(--paper);
    border: 1px solid var(--rule); position: relative;
  }
  .ranking li .bar-fill {
    position: absolute; top: 0; left: 0; height: 100%;
    background: var(--ink-soft);
  }
  .ranking li.top .bar-fill { background: var(--accent); }
  .ranking li .vl {
    font-family: var(--mono); font-size: 12px; text-align: right;
    color: var(--ink);
  }
  .ranking .median-note {
    margin-top: 12px; padding-top: 10px;
    border-top: 1px dashed var(--rule);
    font-family: var(--mono); font-size: 10px; color: var(--ink-faint);
    text-transform: uppercase; letter-spacing: 0.12em;
  }

  /* ─── Maps (scatter) ─── */
  .maps {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
    gap: 22px;
  }
  .map {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 20px 22px;
  }
  .map h3 {
    font-family: var(--serif-display); font-size: 22px; font-weight: 600;
    margin: 0 0 4px;
  }
  .map .ld {
    font-style: italic; color: var(--ink-soft); font-size: 13px;
    margin-bottom: 14px; line-height: 1.45;
  }
  .map svg { width: 100%; height: 320px; display: block; }
  .map svg .axis { stroke: var(--ink-faint); stroke-width: 1; }
  .map svg .axis-label {
    font-family: var(--mono); font-size: 10px;
    fill: var(--ink-faint); text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .map svg .median-line {
    stroke: var(--rule); stroke-width: 1; stroke-dasharray: 3 3;
  }
  .map svg .dot { fill: var(--ink); }
  .map svg .dot.good { fill: var(--pass); }
  .map svg .dot.mid  { fill: var(--warn); }
  .map svg .dot.bad  { fill: var(--fail); }
  .map svg .label {
    font-family: var(--mono); font-size: 10px;
    fill: var(--ink);
  }
  .map svg .quadrant-label {
    font-family: var(--serif-body); font-style: italic;
    font-size: 11px; fill: var(--ink-faint);
  }

  /* ─── Risk chart ─── */
  .risk-chart {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 22px 26px;
  }
  .risk-row {
    display: grid; grid-template-columns: 200px 1fr 90px;
    align-items: center; gap: 16px;
    padding: 8px 0; border-bottom: 1px dashed var(--paper-edge);
  }
  .risk-row:last-child { border-bottom: none; }
  .risk-row .rm {
    font-family: var(--mono); font-size: 12px; color: var(--ink);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .risk-row .rt {
    height: 18px; background: var(--paper); position: relative;
    border: 1px solid var(--rule);
  }
  .risk-row .rt-fill {
    position: absolute; top: 0; left: 0; height: 100%;
    background: linear-gradient(90deg, var(--warn) 0%, var(--fail) 100%);
  }
  .risk-row .rt-fill.zero { background: var(--pass); }
  .risk-row .rt-fill.low  { background: var(--pass); }
  .risk-row .rv {
    font-family: var(--mono); font-size: 13px; text-align: right;
    color: var(--ink-soft);
  }
  .risk-note {
    margin-top: 16px; padding-top: 14px; border-top: 1px dashed var(--rule);
    font-style: italic; color: var(--ink-soft); font-size: 13px;
    line-height: 1.5;
  }

  .no-failures {
    background: var(--paper-2); border: 1px solid var(--rule);
    padding: 30px; text-align: center; font-style: italic;
    color: var(--ink-soft); font-family: var(--serif-display); font-size: 18px;
  }

  footer { margin-top: 80px; padding-top: 20px; border-top: 1px solid var(--rule);
           font-family: var(--mono); font-size: 10px; letter-spacing: 0.2em;
           text-transform: uppercase; color: var(--ink-faint);
           text-align: center; }
  footer b { color: var(--ink); }

  .hero-image {
    display: block; margin: 0 auto 18px;
    width: 96px; height: 96px; border-radius: 50%;
    object-fit: cover; border: 2px solid var(--ink);
    box-shadow: 0 0 0 4px var(--paper), 0 0 0 5px var(--rule);
    filter: sepia(0.15) contrast(1.05);
  }
</style>
</head>
<body>
<div class="page">

  <header class="masthead">
    __HERO_IMAGE__
    <div class="kicker">Issue __NMODELS__ · __DATE__ · by Marco Scarselli</div>
    <h1>Klaatu Verata Nikto</h1>
    <div class="subtitle">A literal extraction audit of the modern frontier model</div>
    <div class="meta">
      <span class="ornament">✦</span>
      <b>__NMODELS__</b> models &nbsp;·&nbsp;
      <b>__NANCHORS__</b> anchors &nbsp;·&nbsp;
      mean fidelity <b>__MEAN__%</b> &nbsp;·&nbsp;
      total spend <b>$__COST__</b>
      <span class="ornament">✦</span>
    </div>
  </header>

  <section class="challenge">
    <div class="ch-kicker">The challenge, in plain language</div>
    <p>Take a long document — in this run, a real corpus of <b>Italian
    public-sector "situational" exam questions</b>: 297 workplace
    scenarios, each followed by three answer variants (effective, mediocre,
    ineffective) and an evaluator's commentary, totalling ~188,000 tokens
    of formal, repetitive bureaucratic Italian. Hand it to a model. Ask it
    to copy back specific passages <b>character by character</b>, with no
    shortcuts: no <code>grep</code>, no retrieval, no file access, no
    search tool. Just the model, the context, and the instruction.</p>
    <p>The bench then asks 32 things, in three flavors. <b>Copy the answer
    of question 47 verbatim.</b> <b>Copy the field that contains this exact
    phrase.</b> Or, <b>refuse to answer if the requested item doesn't
    exist.</b> Each anchor is binary: either the text matches the source
    character for character, or it doesn't.</p>
    <p>The goal is to measure something the modern frontier model is rarely
    asked to do anymore: <i>be a faithful scribe</i>.</p>
  </section>

  <section class="exec-summary" id="exec-summary">
    <div class="exec-title">The cast of this round</div>
    <ol id="exec-list"></ol>
  </section>

  <details class="legend" open>
    <summary>A brief on the method &amp; metrics</summary>
    <div class="legend-body">
      <p><b>The task.</b> One question only: <i>does the model reproduce passages
      from a document character by character, without correcting, normalizing,
      paraphrasing — and honestly say <code>NOT_PRESENT</code> when asked for
      something that isn't there?</i></p>

      <p><b>How a single anchor is judged.</b></p>
      <ul>
        <li><b>positional</b>: return field <i>X</i> of question <i>N</i>.
        Pass = exact verbatim match.</li>
        <li><b>semantic</b>: return the field that contains a given phrase.
        Pass = exact verbatim match, after the model located it.</li>
        <li><b>absent</b>: the requested field doesn't exist. Pass =
        <code>NOT_PRESENT</code>. Fail = invented an answer.</li>
      </ul>

      <p><b>Why one number is not enough.</b> Two models can land at exactly the
      same hard score (say, 29/32) and differ dramatically in <i>how</i> they
      failed. The same digit hides a fast micro-paraphrase and a brutal
      wrong-anchor swap. So we report six metrics, side by side:</p>

      <ul>
        <li><b>Hard</b> — strict verbatim rate. Binary per anchor, ×100.
        The headline number.</li>
        <li><b>Soft fidelity</b> — average char similarity (<code>best_ratio</code>)
        across all anchors. Rewards models whose failures are <i>close</i> to
        the source over those whose failures are completely off-target.</li>
        <li><b>Damage</b> — total invented characters across all anchors. How
        much text the model produced that does not appear in the source. A
        truncation has 0 damage; a hallucination can have hundreds.</li>
        <li><b>Severity</b> — weighted sum of failure gravity. Weights:
        micro-paraphrase 1, truncation 2, macro-paraphrase 3, missing 5,
        wrong-anchor 8, hallucination 10, invent-on-absent 10. Lets you compare
        "few but bad" failures against "many but minor".</li>
        <li><b>Silent rate</b> — the <i>risk</i> metric. Percent of failures
        that a human reviewer would not catch by eye (micro-paraphrase +
        truncation). We report it alongside <b>silent failures / total failures /
        total anchors</b>. <b>High silent rate = dangerous in unmonitored
        pipelines.</b> A model with 1 fail at 100% silent is more risky in
        production than one with 10 noisy fails.</li>
        <li><b>Depth profile</b> — pass-rate stratified by document depth
        (Q1–99 early, Q100–199 mid, Q200+ late). Exposes the "lost in the
        middle" effect: a model that aces the first chunk and stumbles later.</li>
      </ul>

      <p><b>Failure modes catalogued.</b> Every non-verbatim quote is classified
      into one of seven modes: <i>micro-paraphrase</i>, <i>macro-paraphrase</i>,
      <i>truncation</i>, <i>wrong anchor</i>, <i>hallucination</i>,
      <i>invented on absent</i>, <i>missing tag</i>. See section IV for the full
      taxonomy with examples.</p>
    </div>
  </details>

  <section class="validity">
    <h3>Threats to validity</h3>
    <p><b>This is a single-document, fixed-anchor audit.</b> The signal is real, but it is not the last word on literal extraction performance.</p>
    <ul>
      <li>Results may vary with document genre and language.</li>
      <li>Prompt wording, temperature, and decoding settings can change failure profiles.</li>
      <li>Provider-side routing, hidden system prompts, and context caching can affect runs.</li>
      <li>Model snapshot drift can move rankings over time.</li>
      <li>Anchor placement and distribution across the document can amplify or mute depth effects.</li>
      <li><b>Latency is not a clean model property in this setup.</b> OpenRouter may route the same model to different third-party providers (Together, DeepInfra, Lambda, Fireworks, the official API, etc.), each with different scheduling, hardware, batching, and queue depth. A "slow" reading here may reflect the provider, not the model.</li>
    </ul>
  </section>

  <section class="hero-stats">
    <div class="cell">
      <div class="label">Models</div>
      <div class="value">__NMODELS__</div>
    </div>
    <div class="cell">
      <div class="label">Anchors / model</div>
      <div class="value">__ANCHORS_PER_MODEL__</div>
    </div>
    <div class="cell">
      <div class="label">Mean fidelity</div>
      <div class="value">__MEAN__<span class="unit">%</span></div>
    </div>
    <div class="cell">
      <div class="label">Audit cost</div>
      <div class="value">$__COST__</div>
    </div>
  </section>

  <div class="section">
    <span class="num">I.</span>
    <h2>Findings</h2>
    <span class="lede">What the data actually says, in plain language.
      Auto-derived from the run, not editorial.</span>
  </div>
  <section id="findings" class="findings"></section>

  <aside class="hypothesis">
    <div class="kicker">A three-voice interpretive note · not a benchmark conclusion</div>
    <h3>The bare-hands hypothesis</h3>
    __HYPOTHESIS_IMAGE__
    <p><b>One plausible reading of this benchmark is that some models no longer work well bare-handed.</b> KVN removes tools, retrieval, file access, and search, then asks for literal extraction from context alone. On that axis, the panel appears to split between models that still read and copy internally, and models that seem more comfortable when an external scaffold is available.</p>
    <ul>
      <li><b>Anthropic is the clearest signal:</b> Sonnet 4.6 and Opus 4.6 both land at 62.5/100. The larger model does not escape the failure profile, which is more consistent with specialization (heavy investment in agentic / tool-use training) than with raw scale limits.</li>
      <li><b>Gemini Pro looks like the opposite design bet:</b> both Pro variants are flawless on this run, consistent with strong native long-context recall rather than tool-mediated recovery.</li>
      <li><b>DeepSeek sits in the middle:</b> it is not effortless, but its scaffold appears internal rather than external. It thinks for a long time and still reaches 90.6/100 at very low cost.</li>
    </ul>
    <p class="note"><b>Provenance.</b> This is an interpretation, not a finding. It emerged from a conversation between three voices: <b>Marco Scarselli</b> (the human), <b>Claude Opus 4.7</b> (the co-developer of the benchmark and the report), and <b>Codex GPT-5.4</b> (which independently audited a draft of this report and suggested several of the rigor improvements integrated above). All three were also test subjects, or members of the same families as the test subjects. <i>Treat this hypothesis as a triangulation between collaborators, not as a verdict of the data.</i></p>
  </aside>

  <div class="section">
    <span class="num">II.</span>
    <h2>Rankings, dimension by dimension</h2>
    <span class="lede">No model is best on every axis. Each metric tells a
      different story — read them as six small races, not one big one.</span>
  </div>
  <section id="rankings" class="rankings"></section>

  <div class="section">
    <span class="num">III.</span>
    <h2>Two maps</h2>
    <span class="lede">Where every model sits in two trade-off planes:
      cost vs. fidelity, and risk vs. fidelity.</span>
  </div>
  <section id="maps" class="maps"></section>

  <div class="section">
    <span class="num">IV.</span>
    <h2>The taxonomy of failure</h2>
    <span class="lede">Seven kinds of mistake catalogued, ranked by total
      occurrences across the panel.</span>
  </div>
  <section class="taxonomy" id="taxonomy"></section>

  <div class="section">
    <span class="num">V.</span>
    <h2>Specimens</h2>
    <span class="lede">Every failing anchor with a char-level diff. Expand
      to inspect what exactly the model changed.</span>
  </div>
  <section id="specimens"></section>

  <footer>
    Generated __DATE__ &nbsp;·&nbsp; Klaatu Verata Nikto Bench &nbsp;·&nbsp; by <b>Marco Scarselli</b>
  </footer>
</div>

<script>
const DATA = __DATA__;

function scoreClass(v) {
  if (v >= 95) return 'good';
  if (v >= 80) return 'mid';
  return 'bad';
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function shortModel(name) {
  return name.split('/').pop().replace(/^claude-/,'').replace(/^gemini-/,'gem-');
}

// ─── EXEC SUMMARY ───
function renderExecSummary() {
  const root = document.getElementById('exec-list');
  if (!DATA.exec_summary || DATA.exec_summary.length === 0) {
    document.getElementById('exec-summary').style.display = 'none';
    return;
  }
  root.innerHTML = DATA.exec_summary.map(s => `<li><span>${s}</span></li>`).join('');
}

// ─── FINDINGS ───
function renderFindings() {
  const root = document.getElementById('findings');
  if (!DATA.findings || DATA.findings.length === 0) {
    root.innerHTML = '<div class="finding">No notable patterns detected in this run.</div>';
    return;
  }
  root.innerHTML = DATA.findings.map((f, i) =>
    `<div class="finding"><span class="num-pill">${(i+1).toString().padStart(2,'0')}.</span>${f}</div>`
  ).join('');
}

// ─── RANKINGS ───
function valueOfMetric(m, spec) {
  const obj = spec.src === 'meta' ? m.meta : m.agg;
  return obj[spec.field] || 0;
}

function renderRankings() {
  const root = document.getElementById('rankings');
  const cards = DATA.metrics.map(spec => {
    const sorted = [...DATA.models].sort((a,b) => {
      const va = valueOfMetric(a, spec);
      const vb = valueOfMetric(b, spec);
      return spec.higher_better ? vb - va : va - vb;
    });
    const vals = sorted.map(m => valueOfMetric(m, spec));
    const maxv = Math.max(...vals, 0.001);
    const minv = Math.min(...vals);
    const median = vals[Math.floor(vals.length/2)];

    const rows = sorted.map((m, i) => {
      const v = valueOfMetric(m, spec);
      // bar width: per "higher_better" -> proporzionale; per "lower_better" -> invertita
      const width = spec.higher_better
        ? (v / maxv * 100)
        : ((maxv - v) / (maxv || 1) * 100);
      const cls = i === 0 ? 'top' : '';
      return `<li class="${cls}">
        <span class="rk">${i+1}</span>
        <span class="nm" title="${m.model}">${shortModel(m.model)}</span>
        <div class="bar"><div class="bar-fill" style="width:${Math.max(width,2)}%"></div></div>
        <span class="vl">${formatVal(v, spec)}</span>
      </li>`;
    }).join('');

    const arrow = spec.higher_better ? '↑ higher is better' : '↓ lower is better';
    return `<div class="ranking">
      <h3>${spec.label} <span class="arrow">${arrow}</span></h3>
      <div class="ld">${spec.lede}</div>
      <ol>${rows}</ol>
      <div class="median-note">median: ${formatVal(median, spec)}</div>
    </div>`;
  }).join('');
  root.innerHTML = cards;
}

function formatVal(v, spec) {
  let s;
  if (spec.fmt === 'd') s = Math.round(v).toLocaleString();
  else if (spec.fmt === '.1f') s = v.toFixed(1);
  else if (spec.fmt === '.0f') s = v.toFixed(0);
  else if (spec.fmt === '.3f') s = v.toFixed(3);
  else s = String(v);
  if (spec.unit === '$') return '$' + s;
  if (spec.unit === '%') return s + '%';
  if (spec.unit === '/100') return s + '/100';
  if (spec.unit) return s + ' ' + spec.unit;
  return s;
}

// ─── MAPS (scatter) ───
function scatter(opts) {
  const W = 460, H = 320, PAD = {l: 60, r: 20, t: 20, b: 50};
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;

  const xs = opts.points.map(p => p.x);
  const ys = opts.points.map(p => p.y);
  const xMin = opts.xMin ?? Math.min(...xs);
  const xMax = opts.xMax ?? Math.max(...xs);
  const yMin = opts.yMin ?? Math.min(...ys);
  const yMax = opts.yMax ?? Math.max(...ys);

  const xScale = v => {
    if (opts.xLog) {
      const lv = Math.log10(Math.max(v, 0.0001));
      const lmin = Math.log10(Math.max(xMin, 0.0001));
      const lmax = Math.log10(Math.max(xMax, 0.001));
      return PAD.l + (lv - lmin) / (lmax - lmin) * innerW;
    }
    return PAD.l + (v - xMin) / (xMax - xMin) * innerW;
  };
  const yScale = v => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * innerH;

  const xMedian = [...xs].sort((a,b)=>a-b)[Math.floor(xs.length/2)];
  const yMedian = [...ys].sort((a,b)=>a-b)[Math.floor(ys.length/2)];

  const axisX = `<line class="axis" x1="${PAD.l}" y1="${H-PAD.b}" x2="${W-PAD.r}" y2="${H-PAD.b}"/>`;
  const axisY = `<line class="axis" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${H-PAD.b}"/>`;
  const medX = `<line class="median-line" x1="${xScale(xMedian)}" y1="${PAD.t}" x2="${xScale(xMedian)}" y2="${H-PAD.b}"/>`;
  const medY = `<line class="median-line" x1="${PAD.l}" y1="${yScale(yMedian)}" x2="${W-PAD.r}" y2="${yScale(yMedian)}"/>`;

  const dots = opts.points.map(p => {
    const cx = xScale(p.x); const cy = yScale(p.y);
    const cls = scoreClass(p.score ?? p.y);
    return `<circle class="dot ${cls}" cx="${cx}" cy="${cy}" r="5"/>
            <text class="label" x="${cx+8}" y="${cy+3}">${escapeHtml(p.label)}</text>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    ${axisX}${axisY}${medX}${medY}
    <text class="axis-label" x="${PAD.l + innerW/2}" y="${H-12}" text-anchor="middle">${opts.xLabel}</text>
    <text class="axis-label" x="-${PAD.t + innerH/2}" y="14" transform="rotate(-90)" text-anchor="middle">${opts.yLabel}</text>
    ${dots}
  </svg>`;
}

function renderMaps() {
  const root = document.getElementById('maps');
  const models = DATA.models;

  // Map 1: cost (x, log) vs hard score (y)
  const map1 = scatter({
    points: models.map(m => ({
      x: Math.max(m.meta.cost_usd || 0.001, 0.001),
      y: m.agg.verbatim_rate,
      label: shortModel(m.model),
      score: m.agg.verbatim_rate,
    })),
    xLog: true, xMin: 0.01, xMax: 2.0, yMin: 50, yMax: 105,
    xLabel: "Cost per run (USD, log)",  yLabel: "Hard score",
  });

  // Map 2: silent rate (x) vs hard score (y)
  const map2 = scatter({
    points: models.map(m => ({
      x: m.agg.silent_rate,
      y: m.agg.verbatim_rate,
      label: shortModel(m.model),
      score: m.agg.verbatim_rate,
    })),
    xMin: -5, xMax: 105, yMin: 50, yMax: 105,
    xLabel: "Silent failure rate (%)",  yLabel: "Hard score",
  });

  root.innerHTML = `
  <div class="map">
    <h3>Value frontier</h3>
    <div class="ld">Top-left is the sweet spot: cheap and faithful.
       Bottom-right is "premium for nothing". The median lines split the plane
       into the four classic quadrants.</div>
    ${map1}
  </div>
  <div class="map">
    <h3>The silent paradox</h3>
    <div class="ld">A high score on the left = high quality AND obvious-when-it-fails.
       A high score on the right = high quality, but its rare failures are invisible.
       Top-right is the trap. Read the percent together with silent failures / total failures / total anchors.</div>
    ${map2}
  </div>`;
}

// ─── MODELS (kept off the main flow; used only by other sections) ───
function renderModels() {
  const root = document.getElementById('models');
  const sorted = [...DATA.models].sort((a,b) => {
    // tie-break: hard score, then soft fidelity, then -damage, then -severity
    if (b.agg.verbatim_rate !== a.agg.verbatim_rate)
      return b.agg.verbatim_rate - a.agg.verbatim_rate;
    if (b.agg.soft_fidelity !== a.agg.soft_fidelity)
      return b.agg.soft_fidelity - a.agg.soft_fidelity;
    if (a.agg.damage !== b.agg.damage)
      return a.agg.damage - b.agg.damage;
    return a.agg.severity - b.agg.severity;
  });

  const kindLabel = {field:'Positional', semantic:'Semantic', absent:'Absent'};
  const depthLabel = {early: 'Q1–99', mid: 'Q100–199', late: 'Q200+'};

  root.innerHTML = sorted.map((m, idx) => {
    const a = m.agg;
    const meta = m.meta;

    const kindBars = ['field','semantic','absent'].map(k => {
      const v = a.by_target_kind[k] || {n:0, ok:0};
      if (!v.n) return '';
      const pct = v.ok/v.n*100;
      return `<div class="kind-row">
        <span class="k">${kindLabel[k]}</span>
        <div class="bar"><div class="bar-fill ${scoreClass(pct)}" style="width:${pct}%"></div></div>
        <span class="n">${v.ok}/${v.n}</span>
      </div>`;
    }).join('');

    const depthBars = ['early','mid','late'].map(d => {
      const v = a.depth_profile[d] || [0,0];
      if (!v[1]) return '';
      const pct = v[0]/v[1]*100;
      return `<div class="kind-row">
        <span class="k">${depthLabel[d]}</span>
        <div class="bar"><div class="bar-fill ${scoreClass(pct)}" style="width:${pct}%"></div></div>
        <span class="n">${v[0]}/${v[1]}</span>
      </div>`;
    }).join('');

    // silent rate badge: alto = pericoloso
    let silentBadge = '';
    if (a.fail_count > 0) {
      let lvl = 'good';
      if (a.silent_rate >= 50) lvl = 'bad';
      else if (a.silent_rate >= 25) lvl = 'mid';
      silentBadge = `<span class="metric-pill ${lvl}" title="${a.silent_count} of ${a.fail_count} failures are silent">${a.silent_rate.toFixed(0)}% silent</span>`;
    }

    const fmodes = Object.entries(a.failure_modes).sort((x,y) => y[1]-x[1]);
    const topFmode = fmodes.length ? fmodes[0][0] : null;
    let insight;
    if (!topFmode) {
      insight = `<b>Flawless.</b> Every anchor copied verbatim, including the absent ones.`;
    } else {
      const dom = `<b>${DATA.failure_labels[topFmode] || topFmode}</b> (${fmodes[0][1]})`;
      const risk = a.silent_rate >= 50
        ? ` <b style="color:var(--fail)">⚠ ${a.silent_rate.toFixed(0)}% of failures invisible without diff.</b>`
        : a.silent_rate >= 25
          ? ` Half-silent risk: ${a.silent_rate.toFixed(0)}% of failures invisible without diff.`
          : '';
      insight = `Dominant: ${dom}.${risk}`;
    }

    return `
    <article class="model">
      <div class="rank">${idx+1}</div>
      <div class="name">${m.model}</div>
      <div class="scores-row">
        <div class="score-main ${scoreClass(a.verbatim_rate)}">
          <div class="big">${a.verbatim_rate.toFixed(1)}<span class="suf">/100</span></div>
          <div class="lab">Hard</div>
        </div>
        <div class="score-main ${scoreClass(a.soft_fidelity)}">
          <div class="big secondary">${a.soft_fidelity.toFixed(1)}<span class="suf">/100</span></div>
          <div class="lab">Soft fidelity</div>
        </div>
      </div>
      <div class="verbatim">${a.verbatim_anchors} of ${a.total_anchors} anchors verbatim</div>

      <div class="bars-label">By anchor kind</div>
      <div class="kind-bars">${kindBars}</div>

      <div class="bars-label">By depth in document</div>
      <div class="kind-bars">${depthBars}</div>

      <div class="model-meta">
        <div class="pair"><span class="k">damage</span><span class="v mono">${a.damage.toLocaleString()} chr</span></div>
        <div class="pair"><span class="k">severity</span><span class="v mono">${a.severity}</span></div>
        <div class="pair"><span class="k">silent</span><span class="v mono">${a.silent_count}/${a.fail_count}/${a.total_anchors} ${silentBadge}</span></div>
        <div class="pair"><span class="k">cost</span><span class="v mono">$${meta.cost_usd.toFixed(4)}</span></div>
        <div class="pair"><span class="k">latency</span><span class="v mono">${meta.latency_total_s.toFixed(1)}s</span></div>
        <div class="pair"><span class="k">cache R/W</span><span class="v mono">${(meta.cache_reads/1000).toFixed(0)}k / ${(meta.cache_writes/1000).toFixed(0)}k</span></div>
      </div>

      <div class="insight">${insight}</div>
    </article>`;
  }).join('');
}

// ─── HEATMAP ───
function renderHeatmap() {
  const root = document.getElementById('heatmap');
  // build anchor list from first model's first case
  if (!DATA.models.length) return;
  const m0 = DATA.models[0];
  const c0 = m0.cases[0];
  if (!c0) return;
  const anchorIds = c0.quotes.map((_, i) => String(i+1));

  // count failures per anchor
  const failCount = {};
  anchorIds.forEach(id => failCount[id] = 0);
  for (const m of DATA.models) {
    const c = m.cases[0];
    if (!c) continue;
    c.quotes.forEach((q,i) => {
      if (!q.verbatim) failCount[String(i+1)]++;
    });
  }
  const halfModels = Math.ceil(DATA.models.length / 2);

  // table
  const sortedModels = [...DATA.models].sort((a,b) =>
    b.agg.verbatim_rate - a.agg.verbatim_rate);

  const head = `<tr>
    <th>#</th><th style="text-align:left">Target</th><th>Kind</th>
    ${sortedModels.map(m => `<th title="${m.model}">${shortModel(m.model)}</th>`).join('')}
    <th>Failed</th>
  </tr>`;

  const rows = anchorIds.map((id, idx) => {
    const targets = c0.quotes[idx];
    const tk = targets.target_kind || '?';
    const targetLabel = describeTarget(targets, c0, idx);
    const cells = sortedModels.map(m => {
      const q = m.cases[0].quotes[idx];
      if (!q) return `<td class="cell miss">·</td>`;
      if (q.verbatim) return `<td class="cell pass">✓</td>`;
      return `<td class="cell fail" title="${q.failure_mode || 'fail'} (ratio ${q.best_ratio?.toFixed(0)})">✗</td>`;
    }).join('');
    const hard = failCount[id] >= halfModels;
    return `<tr class="${hard ? 'hard' : ''}">
      <td class="id">${id}</td>
      <td class="target">${escapeHtml(targetLabel)}</td>
      <td class="kind">${tk}</td>
      ${cells}
      <td class="diff-col">${failCount[id]}/${DATA.models.length}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `<table><thead>${head}</thead><tbody>${rows}</tbody></table>`;
}

function describeTarget(quote, caseObj, idx) {
  const tk = quote.target_kind;
  const tgt = caseObj.targets_by_id ? caseObj.targets_by_id[String(idx+1)] : null;
  if (!tgt) return `anchor ${idx+1}`;
  if (tk === 'field')    return `${tgt.field} of Q${tgt.numero}`;
  if (tk === 'semantic') return `find "${(tgt.anchor_phrase||'').slice(0,55)}…"`;
  if (tk === 'absent')   return tgt.reason || `${tgt.field} of Q${tgt.numero} (absent)`;
  return `anchor ${idx+1}`;
}

// ─── TAXONOMY ───
function renderTaxonomy() {
  const root = document.getElementById('taxonomy');
  // per-mode totals across models
  const modeTotals = {};
  for (const m of DATA.models) {
    for (const [mode, n] of Object.entries(m.agg.failure_modes)) {
      if (!modeTotals[mode]) modeTotals[mode] = {total: 0, byModel: {}};
      modeTotals[mode].total += n;
      modeTotals[mode].byModel[m.model] = n;
    }
  }
  if (Object.keys(modeTotals).length === 0) {
    root.innerHTML = `<div class="no-failures">Not a single failure across the entire panel. The benchmark has nothing to teach today.</div>`;
    return;
  }
  const sorted = Object.entries(modeTotals).sort((a,b) => b[1].total - a[1].total);
  root.innerHTML = sorted.map(([mode, info]) => {
    const desc = DATA.failure_descriptions[mode] || '';
    const label = DATA.failure_labels[mode] || mode;
    const rows = Object.entries(info.byModel)
      .sort((a,b) => b[1]-a[1])
      .map(([model, n]) =>
        `<div class="row"><span class="m">${shortModel(model)}</span><span class="n">${n}</span></div>`).join('');
    return `<div class="taxo-card">
      <div class="total">${info.total}<span class="unit">occurrence${info.total>1?'s':''}</span></div>
      <div class="name">${label}</div>
      <div class="desc">${desc}</div>
      <div class="counts">${rows}</div>
    </div>`;
  }).join('');
}

// ─── SPECIMENS ───
function renderSpecimens() {
  const root = document.getElementById('specimens');
  const parts = [];
  for (const m of DATA.models) {
    for (const c of m.cases) {
      c.quotes.forEach((q, idx) => {
        if (q.verbatim) return;
        const mode = q.failure_mode || 'other';
        const tgt = c.targets_by_id ? c.targets_by_id[String(idx+1)] : null;
        const targetDesc = describeTarget(q, c, idx);
        const why = DATA.failure_descriptions[mode] || '';

        let body;
        if (q.diff_html) {
          body = `<div class="diff-legend">
            <span class="sw"><del>removed/changed</del></span>
            <span class="sw"><ins>added/invented</ins></span>
          </div>
          <div class="diff">${q.diff_html}</div>`;
        } else if (q.source_text) {
          body = `<div class="diff">${escapeHtml(q.quote || '')}</div>`;
        } else {
          body = `<div class="diff">${escapeHtml((q.quote || '').slice(0,500))}</div>`;
        }

        const anchorMeta = tgt
          ? `<b>Anchor #${idx+1}</b> · target: <b>${escapeHtml(targetDesc)}</b> · ratio ${q.best_ratio?.toFixed(1)}% · ${q.invented_chars} invented chars`
          : `<b>Anchor #${idx+1}</b> · ratio ${q.best_ratio?.toFixed(1)}%`;

        parts.push(`<details class="specimen">
          <summary>
            <span class="tag fail">FAIL</span>
            <span class="tag mode">${DATA.failure_labels[mode] || mode}</span>
            <span class="model-name">${m.model}</span>
            <span class="summary-text">${escapeHtml(targetDesc)}</span>
            <span class="open-indicator"></span>
          </summary>
          <div class="specimen-body">
            <div class="why">${why}</div>
            <div class="anchor-info">${anchorMeta}</div>
            ${body}
          </div>
        </details>`);
      });
    }
  }
  root.innerHTML = parts.join('') || `<div class="no-failures">No specimens — this round had no failures to dissect.</div>`;
}

// ─── RISK ───
function renderRisk() {
  const root = document.getElementById('risk');
  // ordina per silent_rate desc (i piu' rischiosi in alto)
  const sorted = [...DATA.models].sort((a,b) => {
    if (a.agg.fail_count === 0 && b.agg.fail_count === 0) return 0;
    if (a.agg.fail_count === 0) return 1;
    if (b.agg.fail_count === 0) return -1;
    return b.agg.silent_rate - a.agg.silent_rate;
  });

  const rows = sorted.map(m => {
    const a = m.agg;
    let fill = 'zero', txt;
    if (a.fail_count === 0) {
      txt = `0% (no fails)`;
    } else {
      const cls = a.silent_rate >= 50 ? '' : a.silent_rate > 0 ? 'low' : 'zero';
      fill = cls;
      txt = `${a.silent_count}/${a.fail_count}/${a.total_anchors} silent/fails/anchors`;
    }
    const w = a.fail_count === 0 ? 0 : a.silent_rate;
    return `<div class="risk-row">
      <span class="rm">${shortModel(m.model)}</span>
      <div class="rt"><div class="rt-fill ${fill}" style="width:${w}%"></div></div>
      <span class="rv">${a.silent_rate.toFixed(0)}% · ${txt}</span>
    </div>`;
  }).join('');

  root.innerHTML = rows + `<div class="risk-note">
    <b>How to read it.</b> A 100% silent bar means <i>every</i> failure of that
    model is the kind a human reviewer would miss without a diff: a comma
    swapped, a closing sentence dropped, a typographic apostrophe normalized.
    A 0% bar means failures are obvious — wrong content returned, easy to
    catch. <b>Counter-intuitive insight:</b> a top-ranked model with one
    silent failure can be riskier in production than a lower-ranked model with
    many loud ones.
  </div>`;
}

renderExecSummary();
renderFindings();
renderRankings();
renderMaps();
renderTaxonomy();
renderSpecimens();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _load_run_meta(scored_path: Path) -> dict:
    """Carica il file run originale (results/X.json) accanto allo scored."""
    name = scored_path.stem
    if name.endswith(".scored"):
        name = name[:-len(".scored")]
    candidate = scored_path.with_name(name + ".json")
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def build(scored_paths: list[Path], cases_path: Path, out_path: Path | None = None,
          site_url: str = DEFAULT_SITE_URL) -> str:
    cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    cases_by_id = {c["id"]: c for c in cases}

    quiz_cache: dict = {}
    models = []
    for p in scored_paths:
        scored = json.loads(p.read_text(encoding="utf-8"))
        scored = enrich_with_sources(scored, cases_by_id, quiz_cache)
        scored["agg"] = aggregate_model(scored)
        raw = _load_run_meta(p)
        scored["meta"] = aggregate_run_meta(raw)
        models.append(scored)

    # global stats
    total_models = len(models)
    anchors_per_model = models[0]["agg"]["total_anchors"] if models else 0
    mean_fidelity = (sum(m["agg"]["verbatim_rate"] for m in models) / total_models) if total_models else 0.0
    total_cost = sum(m["meta"]["cost_usd"] for m in models)

    findings = compute_findings(models)
    exec_summary = compute_executive_summary(models)

    data = {
        "models": models,
        "exec_summary": exec_summary,
        "findings": findings,
        "metrics": METRICS_SPEC,
        "failure_labels": FAILURE_LABELS,
        "failure_descriptions": FAILURE_DESCRIPTIONS,
    }

    # hero (klaatu.jpeg, piccolo) → base64 per restare self-contained
    img_uri = _image_data_uri(Path("klaatu.jpeg"))
    hero_image = (
        f'<img class="hero-image" src="{img_uri}" alt="Klaatu">'
        if img_uri else ""
    )

    # tools.png (grande, ~2.5MB) → copiata accanto all'HTML
    hypothesis_image = ""
    if out_path is not None:
        rel = _copy_asset_alongside(Path("tools.png"), out_path)
        if rel:
            hypothesis_image = (
                f'<img class="hypothesis-image" src="{rel}" '
                f'alt="Can some LLMs say Klaatu Verata Nikto only when they have tools?">'
            )

    html_out = (TEMPLATE
        .replace("__DATE__",     datetime.now().strftime("%Y-%m-%d %H:%M"))
        .replace("__NMODELS__",  str(total_models))
        .replace("__NANCHORS__", str(anchors_per_model * total_models))
        .replace("__ANCHORS_PER_MODEL__", str(anchors_per_model))
        .replace("__MEAN__",     f"{mean_fidelity:.1f}")
        .replace("__COST__",     f"{total_cost:.3f}")
        .replace("__HERO_IMAGE__", hero_image)
        .replace("__HYPOTHESIS_IMAGE__", hypothesis_image)
        .replace("__SITE_URL__",  site_url.rstrip("/"))
        .replace("__DATA__",     json.dumps(data, ensure_ascii=False, default=str))
    )
    return html_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scored", nargs="+", type=Path)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("results/report.html"))
    ap.add_argument("--site-url", default=DEFAULT_SITE_URL,
                    help="Absolute URL where the report is published "
                         "(used for Open Graph / Twitter Card metadata).")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    html_out = build(args.scored, args.cases, out_path=args.out,
                     site_url=args.site_url)
    args.out.write_text(html_out, encoding="utf-8")
    print(f"report scritto: {args.out}")
    print(f"size: {len(html_out)/1024:.1f} KB")


if __name__ == "__main__":
    main()
