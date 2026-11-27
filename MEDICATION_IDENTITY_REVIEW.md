# Product identity repair — field-evidence-3

Status: draft; hold production rollout. This is one repair following review of
an isolated 52-turn replay. It does not resolve the other clinical blockers.

## Resulting behavior

A small exact-name index supplies checked product identities directly, before
embedding search. It distinguishes Vetameg injectable, Banamine injectable,
Draxxin KP and plain Draxxin injectable. Its label summaries were checked on
2026-11-27 and link to the original label; they are not a complete formulary.
Review summaries whenever labels change. Banamine Transdermal/paste, Draxxin 25,
unclear drug names and other unindexed products must not inherit these labels.

Names are collected from user messages, including earlier explicit spelling or
corrections, rather than inferred from old AI answers. This records references
to products, not confirmation of bottle formulation or administration.

Narrow ingredient/use questions receive a checked, cited information reply
without a model or embedding call. Follow-ups explain the requested component
rather than repeat the full product card. An uncertain formulation, new unknown
product, photo, dose, treatment, combination, withdrawal or symptom question
continues on the model path. This routing is not an emergency classifier.
General medical questions outside the small information vocabulary also continue
on the model path.

Individual clinical requests receive the relevant label summaries as candidate
references and explicit identity context. Their answers remain generated and
unverified. Providing a reference does not prove the model used it correctly;
no claim is made that all medication hallucinations are blocked.

No dose arithmetic, injection procedure or prescribing authorization is added.
The existing citation validator checks provenance, not clinical correctness.

## Validation

- 63 local unit/integration tests pass. Model, embedding and Sheets services are
  stubbed in endpoint tests; this is not a new provider-model run.
- All 52 supplied questions were checked locally for the routing decision.
  Five known problem turns now receive five distinct, source-linked information
  replies: the Vetameg/Banamine correction and the first four drug-identity/KP
  turns. The other 47 require a new model replay after the remaining repairs.
- Coverage includes explicit spelling, conflicting prior AI claims, long user
  history, separate formulations, unknown products, symptom-bearing requests,
  missing/attached image boundaries, and avoiding repeated full-card replies.
- Product-only API results expose `reply_origin=product_information`; the replay
  does not count an all-lookup run as a new model test.
- The isolated rerun overlay includes the new module. Opt-in replay diagnostics
  retain supplied passage IDs, text, source URLs, reply path and model name to
  distinguish retrieval failures from ignored evidence. Normal API responses do
  not expose the prompt or diagnostic passage dump.

## Remaining release blockers

Injection sites and per-site limits; unsupported ration plans and fever-based
reassurance; duration becoming an invented trend; ambiguous dictation creating
another animal; giving in under user pressure; repeated or scolding responses;
semantic support of citations and full evidence coverage. These require changes
and a new model replay. Images, live voice/UI behavior and the Google service
account are separate, still-unverified concerns.

Keep source exports and model replies private. The public repository contains
only generic checks, implementation and label-source links. No live DAVE
container, animal record or production database is changed by this patch.
