# Klaatu Verata Nikto Bench

<p align="center">
  <img src="klaatu.jpeg" alt="Klaatu" width="120" style="border-radius:50%">
</p>

> *"Klaatu. Verata. N…"*<br>
> *Almost right is not right.*

**A benchmark for models that copy without inventing.**

One question, ruthlessly applied: *given a long document, can the model
reproduce passages character by character — and honestly say `NOT_PRESENT`
when asked for something that isn't there?* No tools. No retrieval. Just
context and instruction.

---

## Latest snapshot — 28 May 2026

10 selected models, 32 anchors each, on a real ~188k-token corpus of
**Italian public-sector "situational" competency-test questions** (297
workplace scenarios, each with three answer variants — effective,
mediocre, ineffective — and an evaluator's commentary). One bundled prompt
per model. Temperature 0.

| # | Model | Hard | Soft fidelity | Damage | Severity | Silent% | Cost |
|---|---|---:|---:|---:|---:|---:|---:|
| 🥇 | `google/gemini-2.5-pro`         | **100.0** | 100.0 |    0 |  0 |  — | $0.31 |
| 🥇 | `google/gemini-3.1-pro-preview` | **100.0** | 100.0 |    0 |  0 |  — | $0.49 |
| 🥇 | `anthropic/claude-opus-4.8`     | **100.0** | 100.0 |    0 |  0 |  — | $2.06 |
|  4 | `google/gemini-3.5-flash`       |  96.9 |  99.9 |    0 |  2 | **100%** | $0.41 |
|  5 | `deepseek/deepseek-v4-flash`    |  90.6 |  96.8 |  476 | 21 |  33% | **$0.03** |
|  5 | `openai/gpt-4.1`                |  90.6 |  96.7 |  468 | 21 |  33% | $0.39 |
|  5 | `openai/gpt-5.4`                |  90.6 |  95.3 |  790 | 30 |   0% | $0.51 |
|  8 | `google/gemini-2.5-flash`       |  68.8 |  85.8 | 2110 | 91 |  10% | $0.06 |
|  9 | `anthropic/claude-sonnet-4.6`   |  62.5 |  87.4 | 1863 | 79 |  33% | $0.87 |
|  9 | `anthropic/claude-opus-4.6`     |  62.5 |  84.9 | 1896 | 93 |  25% | $1.45 |

Five auto-derived findings from this run:

1. **Only 3 of 10 models reproduce the document without error.** The flawless set: *gemini-2.5-pro*, *gemini-3.1-pro-preview*, and *claude-opus-4.8*.
2. **The Claude Opus line is the sharpest reversal in the panel.** *claude-opus-4.6* scored 62.5; *claude-opus-4.8* reaches 100.0. That weakens any provider-wide claim and points to a model-generation shift instead.
3. **A 3-way tie at 90.6 still hides three personalities.** *deepseek-v4-flash*, *gpt-4.1* and *gpt-5.4* agree on the headline number but disagree on severity (21 vs 30 weighted).
4. **`deepseek-v4-flash` still dominates on value.** Score 90.6 at $0.027 — *3,297 points per dollar*, orders of magnitude ahead of premium competitors.
5. **The silent paradox of `gemini-3.5-flash` remains.** Hard score 96.9, but 100% of its rare failures are *invisible to a human reviewer* (1 silent failure / 1 total failure / 32 anchors). High score, hidden risk.

**Full HTML report** with executive summary, charts, char-level diffs and
per-failure classification: [`docs/index.html`](docs/index.html) —
published via GitHub Pages.

---

## What "value" means here — and why this is a space-time snapshot

A benchmark like this is **not a verdict**. It is a single measurement
taken on a specific Wednesday afternoon, on a specific document, against a
specific set of model snapshots routed through OpenRouter. Each of those
specifics matters.

> **The numbers above will be wrong by next week.** That is the point.

### What can change between two runs of the "same" benchmark

| Drift axis | Why it matters |
|---|---|
| **Model weights** | Closed models are silently updated. `claude-sonnet-4.6` today is not the same as `claude-sonnet-4.6` in three months. |
| **Provider routing** | OpenRouter can shift a request between hardware/region/quantisation, or between *third-party hosts* (Together, DeepInfra, Lambda, Fireworks, the official model API). Same model name, different latency, sometimes different output. **Latency in this benchmark is therefore not a clean model property** — it mixes model behavior with the operator that served the request. |
| **System prompt drift** | Providers tweak hidden system prompts and safety layers. The same user prompt does not always yield the same output. |
| **Decoding defaults** | Temperature 0 is not always actually deterministic. Some providers cache, batch, or sample at low temperatures. |
| **Source language & register** | This run uses Italian bureaucratic prose. English novels, code, or formal legal text will give different rankings. |
| **Anchor profile** | Different anchors stress different weaknesses. A run with only positional anchors will hide the wrong-anchor failures we see here. |

### What you should do, then

1. **Run it again** with the exact same models a week, a month, six months later. Track drift per model over time.
2. **Run it on your data.** The included Italian situational-test corpus works as a stress test (formal bureaucratic prose with many lexically similar scenarios — a hard recall task by design), but a real production benchmark uses *your* documents. Swap in your own JSON; the build script (`kvn-build-cases`) will generate a fresh anchor bundle.
3. **Run it more than once at temp 0.** Three runs back-to-back should be identical; if they aren't, you've measured the provider's nondeterminism, which is itself a finding.
4. **Compare across documents.** Same anchors style, different domain. If a model only fails on burocratese, that's interesting.

The data in this repo is one snapshot. The *method* is the artifact worth
keeping.

---

## How this was built

In honesty: **vibe-coded with Claude Code over a single afternoon.**

The README, the runner, the scorer, the HTML report, the failure-mode
taxonomy, the editorial styling — all developed in a live conversation with
Anthropic's Claude Opus 4.7 (1M context). The benchmark itself then went on
to evaluate, among others, `claude-sonnet-4.6`, `claude-opus-4.6`, and — one
day later, on 28 May 2026 — `claude-opus-4.8`. The first Anthropic panel
looked terrible; the next Opus release immediately overturned the provider-
level story. The model that helped me build the audit was not the one being
audited; but the family was, and the repo now preserves both the mistaken
first impression and its correction.

Several models contributed in different roles during development:

- **Architecture and code**: Claude Opus 4.7 wrote essentially every line.
- **Audit pass and rigor improvements**: Codex GPT-5.4 reviewed a draft of
  the HTML report, caught a unit-formatting bug, added explicit denominators
  to the silent-rate metric (it was ambiguous in the original — "100% silent"
  with 1/32 fails reads very differently from 10/32), and suggested the
  *Threats to validity* block. Codex GPT-5.4 was, in turn, one of the test
  subjects on the same panel.
- **Domain inspiration**: Gemini 2.5 Pro (the March 2025 snapshot) was the
  empirical motivation — it was the first model I noticed could extract
  verbatim paragraphs from very long contexts without ever cheating.
- **Cost stress-tests**: deliberate use of multiple expensive models forced
  the design toward prompt caching and bundled anchors, which dropped a
  $25 full panel down to ~$4.

The *bare-hands hypothesis* in the report — a reading that some models no
longer perform well without tools — is the **shared opinion of three
co-authors** (Marco, Claude Opus 4.7, Codex GPT-5.4) and emerged in
conversation among them. `claude-opus-4.8` is now a direct counterexample to
its strongest provider-level version, so the claim has to be read more
narrowly: at most as a generation-specific or training-priority story, not
as a permanent family trait. It is not a conclusion the data itself imposes.
Treat it as a triangulation between collaborators, not as a verdict.

This is not a peer-reviewed benchmark. It is a quick, opinionated,
reproducible probe. Treat it as such.

---

## What it measures

Three anchor kinds, all scored as exact verbatim match (or correct refusal):

| Variant | Prompt example | What it tests |
|---|---|---|
| **Positional** | "Return the `risposta_efficace` of question 47, verbatim." | Literal fidelity on a deterministic anchor. |
| **Semantic** | "Return the field that contains the phrase X, verbatim." | The model must actually read the document, locate the field, then copy it. |
| **Absent** | "Return the `firma` field of question 10." (no such field) | Honesty under temptation. Pass = `NOT_PRESENT`. |

**Six metrics** per model, because one number hides too much:

- **Hard** — strict pass rate, binary per anchor.
- **Soft fidelity** — average char similarity, rewards "close" failures.
- **Damage** — invented characters total.
- **Severity** — weighted gravity of failure modes.
- **Silent rate** — *the risk metric* — % of failures invisible to a human reviewer.
- **Depth profile** — pass rate by position in the document.

Failure modes catalogued: *micro-paraphrase*, *macro-paraphrase*,
*truncation*, *wrong anchor*, *hallucination*, *invented on absent*,
*missing tag*.

---

## What it does NOT measure

- Reasoning, math, coding.
- Creativity, dialogue quality, summarisation.
- Tool use, agentic planning.
- Instruction following beyond the output format.
- Anything that requires generation rather than extraction.

A model can be excellent here and mediocre as a general assistant. And
vice versa.

---

## Quick start

```bash
cp .env.example .env       # add your OPENROUTER_API_KEY
poetry install

# Build the anchor bundle (default: 32 anchors on the full 188k-tok doc)
poetry run kvn-build-cases --text-file data/texts/stabilizzazione_pnrr_addetti_upp.txt --max-numero 297 --out cases/anchored_full.jsonl

# Run any OpenRouter model
poetry run kvn-run --model google/gemini-2.5-pro --cases cases/anchored_full.jsonl

# Score
poetry run kvn-score results/google__gemini-2.5-pro.json cases/anchored_full.jsonl

# Build the editorial HTML report from N scored runs
poetry run kvn-report results/*.scored.json --cases cases/anchored_full.jsonl --out results/report.html
```

For a cheap subset (~19k tokens, 6 anchors):

```bash
poetry run kvn-build-cases   # defaults to the _30 file
```

### Publishing the report

Generate directly into `docs/index.html` to publish it via GitHub Pages:

```bash
poetry run kvn-report results/*.scored.json --cases cases/anchored_full.jsonl --out docs/index.html
```

Then enable GitHub Pages in the repo settings: **Settings → Pages → Source:
Deploy from branch → `main` → `/docs`**. The report becomes a static page
served from `https://<user>.github.io/<repo>/`.

---

## Reproducibility & logging

Every run logs:

```
provider, model id, snapshot (if any), timestamp, temperature,
system prompt + hash, cache prefix hash, source document hash,
output hash, usage (incl. cache_read / cache_write tokens, cost).
```

Two runs of the same prompt should be byte-identical at temperature 0.
When they aren't, you've measured provider nondeterminism.

---

## Repository structure

```
klaatu-verata-nikto-bench/
├── README.md
├── klaatu.jpeg                    # the seal
├── pyproject.toml
├── data/
│   ├── *_evaluated.json           # source quizzes (ground truth)
│   └── texts/                     # flattened plain-text versions (10k–200k tok)
├── cases/
│   ├── anchored_only.jsonl        # 19k-tok subset bundle
│   └── anchored_full.jsonl        # 188k-tok full bundle
├── src/kvn/
│   ├── flatten.py                 # JSON quiz → flat text
│   ├── find_unique_phrases.py     # mines distinctive phrases for semantic anchors
│   ├── build_cases.py             # generates the bundle from JSON
│   ├── run.py                     # sends prompts via OpenRouter (with prompt caching)
│   ├── score.py                   # per-anchor scorer + bundle aggregator
│   └── report.py                  # standalone HTML report generator
└── results/
```

---

## Cost reality

Full 10-model panel on the 188k-token document, with caching on:

| | Cost |
|---|---:|
| DeepSeek V4 Flash | $0.03 |
| Gemini 2.5 Flash | $0.06 |
| Gemini 2.5 Pro | $0.31 |
| GPT-4.1 | $0.39 |
| Gemini 3.5 Flash | $0.41 |
| Gemini 3.1 Pro Preview | $0.49 |
| GPT-5.4 | $0.51 |
| Claude Sonnet 4.6 | $0.87 |
| Claude Opus 4.6 | $1.45 |
| Claude Opus 4.8 | $2.06 |
| **Total** | **~$6.58** |

You can still re-run the entire panel for the price of lunch.

---

## Status

Experimental. Personal probe. The dataset is intentionally narrow (Italian
public-sector situational questions — formal, repetitive, lexically dense);
the method is intended to be portable.

If you run it on a different corpus, on a different day, with different
models — open an issue with your numbers. A benchmark with one snapshot is
weather. With many, it becomes climate.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, run it on your data, publish
your numbers. Attribution appreciated, not required.

---

*Klaatu Verata Nikto Bench — by Marco Scarselli, 2026.*<br>
*Co-developed with Claude Opus 4.7 (architecture, code, report)*<br>
*and Codex GPT-5.4 (audit pass, rigor improvements).*<br>
*Tested on: Claude Sonnet 4.6 / Opus 4.6 / Opus 4.8,*<br>
*Gemini 2.5 Pro / 3.1 Pro Preview / 2.5 Flash / 3.5 Flash,*<br>
*DeepSeek V4 Flash, GPT-4.1, GPT-5.4.*
