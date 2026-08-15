# AI Component Classification (OpenRouter)

The pipeline can automatically assign each CAD part a component type (top
shell, bottom shell, scroll wheel, PCB, button, battery, …) using the
OpenRouter API. The classifier is **vision-first**: it renders per-part
orthographic thumbnails (stdlib-only z-buffer rasterizer → PNG) and asks a
vision model to label each part, fusing that with a deterministic rule
classifier (existing name-synonym + topology classifier) and any user-reviewed
roles.

## Enabling

The AI stage is **off** by default. Set both env vars on the server:

```bash
export OPENROUTER_API_KEY="sk-or-..."
export MOUSE_SIM_AI_ENABLED=1
```

Optional:

| Env var | Default | Meaning |
|---|---|---|
| `MOUSE_SIM_AI_MODEL` | `openai/gpt-4o-mini` | OpenRouter model id |
| `MOUSE_SIM_AI_MAX_PARTS` | `64` | Cap on AI-classified parts per run (beyond → heuristic) |
| `MOUSE_SIM_AI_TIMEOUT_S` | `45` | Per-call HTTP timeout |
| `MOUSE_SIM_AI_CACHE_DIR` | `.web-cache/ai_classify` | Per-part result cache |
| `MOUSE_SIM_AI_CACHE_CAPACITY` | `500` | LRU cache entry cap |

Rollback: unset `OPENROUTER_API_KEY` (or `MOUSE_SIM_AI_ENABLED`) → the
pipeline falls back to the deterministic rule classifier, byte-for-byte the
pre-AI behavior. Delete the cache dir to clear all AI results.

## How it works

1. **Descriptors** (`ai_classify.part_descriptors`): normalized name + numeric
   geometry vector (bbox/aspect ratios, flatness, volume, surface area,
   footprint coverage, mirror symmetry, topology flags).
2. **Thumbnails** (`ai_classify.render_part_thumbnail`): top/front/side
   orthographic triptych PNG, rendered with a stdlib z-buffer rasterizer
   (`zlib` + `struct` — no Pillow/numpy).
3. **OpenRouter call** (`ai_classify.call_openrouter`): `chat/completions`
   with `temperature: 0`, images as data URLs, JSON output; retries with
   backoff on 429/5xx/network.
4. **Consensus** (`ai_classify.merge_classification`): user request > AI ≥0.85
   agreeing with rule > rule on AI disagreement (with `needs_review`) > AI
   <0.85 (with `needs_review`) > heuristic fallback.
5. **Cache**: per-part disk cache keyed by a content hash (model + prompt
   version + descriptors + thumbnail). Re-runs are free and deterministic.

## Privacy

Only the **part name, the descriptor vector, and the rendered thumbnails**
leave the machine. Full meshes are never sent to OpenRouter.

## Web UI

- **Model tree**: "AI Classify" button starts a job; per-row `AI 92%` badges
  show suggestions; "Apply all" commits reviewed roles.
- **Inspector**: AI suggestion card with Apply/Dismiss next to the role
  selector; reasons shown in the tooltip.
- Reviewed roles ride along on the analyze request
  (`objects[].classification`) and win at the pipeline stage.

## API

- `POST /api/classify` `{asset_id, part_ids?}` → `202 {job_id}`
- `GET /api/classify/jobs/{job_id}` → status + results

## Evaluation

`python3 scripts/eval_classify.py --mode offline` runs the deterministic
baseline against the hand-labeled G3 golden set
(`reference/ai_classify_labels.json`, 46 parts). `--mode live` runs the real
OpenRouter fusion (requires the env vars) and writes
`reports/ai_classify/live_results.json`. Acceptance bar: ≥85% exact-role
accuracy on G3, review burden ≤30%.

## Troubleshooting

- **429s**: batch sizes halve automatically; pause between batches is 250 ms.
- **Non-determinism**: `temperature: 0` + the disk cache; fresh uncached runs
  may still differ — the cache makes repeated runs stable.
- **Missing key**: the job still completes with heuristic results and an
  `AI_CLASSIFY_DISABLED`-style finding; the server never errors on a missing
  key.
