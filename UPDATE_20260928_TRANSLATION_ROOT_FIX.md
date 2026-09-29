# Factory translation source-alignment update (2026-09-29)

Replace the matching files in the existing project root with the files in this archive. The archive contains only changed files.

## What changed

- Factory-language translations now use source-alignment reasoning: preserve who/what a modifier applies to, distinguish stated facts from inference, and avoid adding unsupported details.
- Factory term knowledge now covers relevant production concepts and proper-name handling, including 異型棒, 大成, and 爐號. These are reusable term/context rules, not sentence-specific substitutions.
- The shared quantity frame now captures explicit lot references such as 這批料 even when there is no numeral. The same frame informs the first model prompt and flags translations that drop the lot/batch scope.
- The general factory prompt now separates checking an existing process state from performing a process, system stock-entry records from physical material movement, and pending/completed steps, responsibilities, and destinations.
- Reported findings now retain both the person who reports an issue and the person who discovers it. When Chinese leaves the discovery subject implicit and the reporter is the supported topic, prompts and both acceptance gates keep that person attached to the finding; an explicitly different finder stays distinct. This is a reusable event-role rule, not a stored sentence translation.
- Factory translation requests no longer use the fast-quality shortcut. The existing single-provider-call flow remains in place; supported GPT reasoning models use low reasoning effort for this route.
- Translation quality checks now flag an anatomical injury detail, such as “hand,” when the source does not specify it.
- Indonesian work-order validation recognizes urgency/material/workflow modifiers in the noun phrase.

## Validation

The factory translation asset release validator passed. Updated Python files passed `py_compile`. All 41 focused tests passed: 6 reported-event role tests, 15 quantity-semantics tests, 11 unified-policy tests, and 9 factory operational-status tests. Direct validation rejects both the screenshot's agentless discovery and a translation that drops 這批料. The complete offline release-gate runner could not start in the local runtime because `requests`, pytest, and Flask are unavailable.
