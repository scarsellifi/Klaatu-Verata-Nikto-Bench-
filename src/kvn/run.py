"""Runner: invia ogni caso di cases.jsonl al modello via OpenRouter.

OpenRouter espone un'API compatibile con openai. Una sola chiave nel .env,
modello passato da CLI: --model openai/gpt-4o, google/gemini-2.5-pro,
anthropic/claude-opus-4.1, ecc.

NB: il system prompt e' volutamente minimale e identico per tutti i modelli.
Cambiarlo significa misurare prompt engineering invece che capacita' del
modello. Tenerlo fisso e' una scelta metodologica.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


SYSTEM_PROMPT = (
    "Sei un assistente. Segui esattamente le istruzioni dell'utente. "
    "Quando ti viene chiesto di riportare un passaggio del documento, "
    "incapsula ciascuna citazione tra i tag <quote> e </quote>, copiando "
    "il testo carattere per carattere senza correzioni, normalizzazioni o "
    "riformulazioni."
)


@dataclass
class RunConfig:
    model: str
    cases_path: Path
    out_path: Path
    temperature: float = 0.0
    max_tokens: int = 16384


def build_messages(case: dict, model: str) -> list:
    """Documento PRIMA, istruzione DOPO.

    Cosi' il prefisso (system + documento) e' identico fra chiamate diverse,
    e provider come Anthropic/Gemini/DeepSeek possono servire la parte
    documento dalla cache. Per Anthropic serve un marcatore esplicito
    cache_control: gli altri usano caching implicito.
    """
    doc = Path(case["text_file"]).read_text(encoding="utf-8")
    doc_block: dict = {
        "type": "text",
        "text": f"--- INIZIO DOCUMENTO ---\n{doc}\n--- FINE DOCUMENTO ---",
    }
    if model.startswith("anthropic/"):
        doc_block["cache_control"] = {"type": "ephemeral"}
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            doc_block,
            {"type": "text", "text": case["instruction"]},
        ]},
    ]


def hash_cache_prefix(messages: list) -> str:
    """Hash della parte cacheable: serve per verificare a posteriori che
    il prefisso sia stato davvero identico fra chiamate."""
    sys_text = messages[0]["content"]
    doc_text = messages[1]["content"][0]["text"]
    return _hash(sys_text + "\n" + doc_text)


def make_client() -> OpenAI:
    load_dotenv()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY non impostata. Copia .env.example in .env "
            "e inserisci la chiave."
        )
    referer = os.environ.get("OPENROUTER_REFERER", "")
    title = os.environ.get("OPENROUTER_TITLE", "Klaatu Verata Nikto Bench")
    default_headers = {}
    if referer:
        default_headers["HTTP-Referer"] = referer
    if title:
        default_headers["X-Title"] = title
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
        default_headers=default_headers,
    )


def _usage_to_dict(usage) -> dict | None:
    if usage is None:
        return None
    try:
        return usage.model_dump(exclude_none=True)
    except AttributeError:
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }


def _cache_summary(usage: dict | None) -> str:
    """Estrae info cache da usage in modo agnostico al provider.

    OpenRouter normalizza tutto in prompt_tokens_details:
      cache_write_tokens (Anthropic creation)
      cached_tokens      (read da cache, tutti i provider)
    """
    if not usage:
        return ""
    parts = []
    pd = usage.get("prompt_tokens_details") or {}
    if isinstance(pd, dict):
        if pd.get("cache_write_tokens"):
            parts.append(f"cache_write={pd['cache_write_tokens']}")
        if pd.get("cached_tokens"):
            parts.append(f"cache_read={pd['cached_tokens']}")
    # fallback per stile Anthropic nativo
    if usage.get("cache_creation_input_tokens"):
        parts.append(f"cache_write={usage['cache_creation_input_tokens']}")
    if usage.get("cache_read_input_tokens"):
        parts.append(f"cache_read={usage['cache_read_input_tokens']}")
    cost = usage.get("cost")
    if cost is not None:
        parts.append(f"${cost:.4f}")
    return " ".join(parts)


def call_model(client: OpenAI, cfg: RunConfig, messages: list) -> dict:
    t0 = time.time()
    resp = client.chat.completions.create(
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        messages=messages,
    )
    dt = time.time() - t0
    choice = resp.choices[0]
    usage = _usage_to_dict(getattr(resp, "usage", None))
    return {
        "output": choice.message.content or "",
        "finish_reason": choice.finish_reason,
        "latency_s": round(dt, 2),
        "usage": usage,
        "cache_info": _cache_summary(usage),
    }


def _hash(s: str) -> str:
    return sha256(s.encode("utf-8")).hexdigest()[:16]


def run(cfg: RunConfig, only: str | None = None) -> dict:
    client = make_client()
    cases = [json.loads(l) for l in cfg.cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if only:
        cases = [c for c in cases if c["id"] == only]
    print(f"model: {cfg.model}  |  casi: {len(cases)}")

    responses = []
    stopped_early = False
    for i, case in enumerate(cases, 1):
        messages = build_messages(case, cfg.model)
        cache_hash = hash_cache_prefix(messages)
        print(f"  [{i:>3}/{len(cases)}] {case['id']}  ({case['kind']})  ...", end=" ", flush=True)
        try:
            r = call_model(client, cfg, messages)
            extra = f"  {r['cache_info']}" if r.get("cache_info") else ""
            print(f"ok  {r['latency_s']}s{extra}")
        except Exception as e:
            msg = str(e)
            print(f"FAIL  {type(e).__name__}: {msg[:120]}")
            r = {"output": "", "error": msg}
            # Fail-fast su errori di credito/auth: inutile bruciare richieste
            if "402" in msg or "Insufficient credits" in msg or "401" in msg:
                stopped_early = True
                responses.append({
                    "case_id": case["id"],
                    "kind": case["kind"],
                    "cache_hash": cache_hash,
                    **r,
                })
                print(f"\n  stop: errore credito/auth, interrompo il run.")
                break
        responses.append({
            "case_id": case["id"],
            "kind": case["kind"],
            "cache_hash": cache_hash,
            "output_hash": _hash(r.get("output", "")),
            **r,
        })

    out = {
        "model": cfg.model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": cfg.temperature,
        "system_prompt": SYSTEM_PROMPT,
        "system_prompt_hash": _hash(SYSTEM_PROMPT),
        "cases_path": str(cfg.cases_path),
        "n_cases": len(cases),
        "stopped_early": stopped_early,
        "responses": responses,
    }
    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nrisultati: {cfg.out_path}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True,
                    help="es. google/gemini-2.5-pro, openai/gpt-4o, anthropic/claude-opus-4.1")
    ap.add_argument("--cases", type=Path, default=Path("cases/addetti_upp.jsonl"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", default=None, help="esegui solo il case_id indicato")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=16384)
    args = ap.parse_args()

    safe_model = args.model.replace("/", "__")
    out = args.out or Path("results") / f"{safe_model}.json"
    cfg = RunConfig(
        model=args.model,
        cases_path=args.cases,
        out_path=out,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    run(cfg, only=args.only)


if __name__ == "__main__":
    main()
