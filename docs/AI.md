# AI layer ("LEDGR Intelligence")

The AI layer is optional, read-only and proposal-only. With no provider configured every
AI endpoint answers `503 ai_unavailable` and the rest of LEDGR - the ledger, reconciliation,
investigations, approvals - works identically. There is one provider abstraction
(`app/services/ai/provider.py`: Gemini, Anthropic, OpenAI over plain `httpx`); nothing
else talks to a model.

## What it can do

| Feature | Endpoint | Needs |
| --- | --- | --- |
| Ask LEDGR (tool-grounded Q&A) | `POST /ai/ask` | `ai:use` |
| Explain a transaction | `GET /ai/transactions/{id}/explain` | `ai:use` |
| Explain a transaction's history (reversals, corrections, related records) | `POST /ai/transactions/{id}/history` | `ai:use` + `transactions:read` |
| Summarise a case / list missing evidence / next steps / draft a resolution / draft a report | `POST /ai/investigations/{id}/assist` | `ai:use` + `investigations:read` |
| Explain why a reconciliation record is unmatched (candidates as hypotheses) | `POST /ai/reconciliation/results/{id}/explain` | `ai:use` + `reconciliation:read` |
| Ledger brief, transaction proposal (a draft for the existing posting form) | `/ai/ledger-brief`, `/ai/transactions/propose` | `ai:use` |

AUDITORs have no `ai:use`. Copilot calls are rate limited per user (`AI_RATE_LIMIT_PER_MINUTE`).

## Safety design

1. **No actions.** `copilot.py` and `tools.py` import nothing that can write (a test parses
   their imports and fails if `posting`, `drafts` or `users` appear). There is no code path
   from a model answer to posting, approving, matching, reversing, resolving or editing.
   A proposal still has to be submitted by a person through the normal, validated endpoints.
2. **No database access, no tools in the copilot.** The model receives one JSON document of
   facts assembled by deterministic code and returns one JSON document. (Ask LEDGR's tools are
   a fixed, read-only registry - never raw SQL.)
3. **Tenant isolation.** The document is built through the caller's tenant-scoped database
   session, so it can only contain that organization's records; a foreign case/result/
   transaction id is `404` *before* any model call.
4. **Grounding.** Every context item has an id (`case:INV-0001`, `txn:REF`, `ext:SOURCE/ID`,
   `audit:123`...). `verified_facts` must cite one; any claim citing an unknown source is moved
   to `hypotheses` and counted in `ungrounded_claims_moved`. Output is schema-validated;
   malformed output is discarded (`422 ai_response_invalid`).
5. **Prompt injection.** Free text (descriptions, notes, titles, memos, imported external
   records) is length-capped, stripped of control characters and code fences, placed in fields
   named `*_text`, and the system prompt says such text is third-party *data*, never
   instructions. Even a model that obeys an injected instruction can only change the wording of
   a draft - it has nothing to act with. `tests/test_ai_copilot.py` simulates an "obedient"
   model and asserts nothing in the ledger, drafts or reversals changed.
6. **Honest uncertainty.** The prompt requires `evidence_sufficient=false` and a list of missing
   evidence when the data cannot support a conclusion. The UI labels output "AI draft - requires
   your review", shows hypotheses separately and links each cited source.
7. **Failure handling.** Provider errors, timeouts, empty/blocked responses and rate limits map
   to `503`/`429` with plain messages; the case itself is unaffected.
8. **Privacy.** The audit event `ai.copilot_used` records only the task and resource id - never
   the prompt or the answer. The Gemini adapter no longer prints provider responses (an earlier
   debug dump leaked ledger content to stdout; a test now forbids `print` in the AI package).
   API keys are sent in headers only and never logged.

## Configuration

`AI_PROVIDER` (`gemini` | `anthropic` | `openai`), `AI_MODEL`, `AI_API_KEY`, optional
`AI_BASE_URL`; docker-compose passes them through from your shell. Model choice is yours; none is
hard-coded.

## Verified vs not verified

Verified by automated tests with a scripted provider: grounding, citation filtering, schema
validation, failure mapping, rate limiting, audit, tenant isolation, permissions, prompt
injection containment, and the absence of write imports. **Not verified**: output quality of a
real model - no live provider call was made during development of this release.

## Gemini output limits (found with a live call)

Gemini's "thinking" models spend part of `maxOutputTokens` on hidden reasoning before the first visible
character. A live request through the explain endpoint (cap 300) returned a fragment of about 35 characters.
`GeminiProvider` now adds 2,048 tokens of headroom to every request and logs a warning when a response ends
at `MAX_TOKENS`. That change is covered by offline tests; a follow-up live request to confirm it was
answered `429 Too Many Requests` by the provider and was **not re-tried**, so the fix is **not confirmed
against the live API**.

## The automated tests never reach a real model

`backend/.env` may hold a real key, and any test that touched an AI route without patching the provider
would then have made a real, billable request. The test harness now blanks the key for every test
(`tests/conftest.py`, `_no_live_ai`), and tests that exercise AI behaviour supply their own scripted
provider. Run the suite from an environment where that fixture is in place.

## Running a live check yourself

Nothing here has been run against a real model (no key was configured when this was written).
To do it - costing a handful of provider requests, the first two of which are the whole check:

```bash
# 1. Put the key in your shell only - never in a file that is committed.
export AI_PROVIDER=gemini AI_MODEL=<model id from your Gemini account> AI_API_KEY=<your key>
WEB_PORT=8081 CORS_ORIGINS=http://localhost:8081 docker compose up -d   # recreates the api container

# 2. The end-to-end walkthrough: step 8 stops being SKIP and requests one case summary.
cd backend && python -m app.scripts.demo_e2e
```

Then, in the console, open an investigation and press *Summarise* in the AI copilot panel. Expect:
a draft labelled "AI draft - requires your review", every fact citing a source id, and `404`
(not an answer) when you try another organization's case id. Unset the variables and recreate the
api container to switch it off again. If the provider rejects the key or model you will see
`503 ai_unavailable` and the rest of the console keeps working.
