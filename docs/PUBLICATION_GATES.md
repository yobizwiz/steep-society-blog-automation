# Publication gates and offline review

This document supersedes older README examples where they conflict. The supported release paths are `src/main.py`, `src/weekly.py`, `src/run_step.py` (candidate finalization), and `src/publish_step.py`.

## Behavior

Normal articles can pass automatically. There is **no mandatory human approval or approval hash**. The five editorial dimensions must each be numeric and at least 8. A score is never evidence of factual accuracy. Structural validation, independent source-grounded factual review, verified images, and final HTML review must also pass. Errors become a failed run or `review_required`; the runner does not force a 10/10 score.

Generation and final publication each perform factual review. Ordinary subjective editorial advice needs no external source. Consequential safety, measurement, product, inventory, policy, experience and ranking claims require applicable primary evidence. Curate short, dated, verifiable snapshots in `config/evidence/`; never add invented records to force acceptance. Missing, expired or mismatched evidence holds the candidate. The reviewer is a model: coverage and semantic accuracy still require evaluation. A snapshot hash proves file integrity, not truth or provenance. Publication does not fetch model-supplied source URLs.

The schedule owns the CTA destination. Explicit product entries may use that product; otherwise use the scheduled collection. No arbitrary bestseller fallback is inserted after review. CTA recognition uses semantic markup/buttons rather than background color; new content should use `data-cta="primary"`.

Images must pass vision review before any image is uploaded. After insertion, spacing, link cleanup and JSON-LD image synchronization, the exact final HTML is validated, fact-reviewed and saved as `output/<slug>-final.{json,html}` plus `.check.json`. Upload records bind verified images to final URLs. All surviving internal links must return a successful status; transport uncertainty blocks publication.

Blog lookup and article lookup are scoped and paginated. Dates are compared in UTC. A local store/blog lock and shared GitHub Actions concurrency group serialize supported writers. A second duplicate check runs immediately before creation. A persistent receipt is written before the draft POST, including ambiguous attempts. Draft creation is followed by schedule/publish mutation and state/body readback; `userErrors` or mismatch is failure. Updates preserve existing publication state in the same mutation. Public report URLs use the configured blog handle.

## Offline checks

From the repository root, with Python 3.11 or newer:

```text
python -X utf8 -m unittest discover -s tests -v
git diff --check
```

The tests deny socket/urllib network access before project imports and mock generation, vision, source review and Shopify transports. No credentials, paid generation or live publishing are needed. **`main.py --dry-run` still uses paid text generation/review and is not an offline test.** Do not use `verify_keys.py` for an offline check.

## Status and recovery

`success` means the requested write and final readback passed. `already_exists` is a normal no-op. `missing_schedule`, `review_required`, and `failed` are distinct failures and cause nonzero weekly exit status. `--days` applies even when `--start` is absent. The default start is next Monday; weekly schedule is 07:00 UTC. The single-day CLI keeps its own explicit schedule-time option.

On a receipt marked `creating`, `created_draft`, or `reconciliation_required`, inspect the stored article ID, target blog/date/handle and actual remote state before retrying. Do not delete receipts or stale locks merely to bypass the gate. This change does not automatically unpublish or delete a draft after an ambiguous result. Receipts are local artifacts, not a distributed idempotency service; a new runner must restore relevant receipts or reconcile Shopify first. Independent machines/repositories sharing a blog are not protected by the local lock.

## Review boundaries

- No live API or paid model run validated this branch. Latest Shopify GraphQL schema validation is separate from end-to-end integration testing. Default API version is 2026-10 and can be set with `SHOPIFY_API_VERSION`.
- Source libraries are intentionally small; ordinary advice can pass, but unsupported factual topics will hold until applicable evidence is supplied or claims are rewritten. No blanket keyword ban is used.
- Legacy repair/refine/revary/review-upgrade entry points and workers fail before API calls, including paid generation and uploads. STEEP/SERA schedule triggers are removed locally; dispatch definitions remain, but these tools need migration before they can run. Image SEO patching is report-only. Historical date-repair and GSC helpers remain outside the supported publication protocol.
- Existing Shopify articles are not repaired by deploying this code. The Oct 5–11 candidates require separate review and separately authorized live changes.

## Follow-up review fixes (2026-10-03)

Final release requires trusted ISO date and HTTPS scheduled CTA context before transformation or network checks. The CTA must contain exactly one anchor equal to that destination. Editorial improvement receives the CTA explicitly and cannot choose another collection.

Factual review requires a complete end_turn response and strict JSON; no repaired, fenced, duplicate-key or non-finite JSON is accepted. Empty claims/issues alongside salient safety or technical language is incomplete coverage. This is not a content ban: supported warnings and rejected unsafe quotations can pass. Ordinary advice can pass an empty review. The backstop does not prove exhaustive coverage, especially when another claim was reviewed; no measured model accuracy or pass rate is claimed.

Supported image callers require explicit IMAGEN_MODEL, with no inconsistent default or silent preview-model substitution. Model availability and account entitlement require a separately scoped live check. 403/429 link results fail verification without being treated as confirmed 404 removals. Known final-stage duplicate HEAD requests are retained rather than weakening verification.

Source snapshots prove only their exact cited facts and conditions. Never use a store policy or catalog as independent safety evidence. Read-only snapshot curation may be blocked by storefront throttling; missing evidence is not replaced with guessed text.
