# Factory translation source-alignment update (2026-09-29)

Replace the matching files in the existing project root with the files in this archive. The archive contains only changed files.

## What changed

- Factory-language translations now use source-alignment reasoning: preserve who/what a modifier applies to, distinguish stated facts from inference, and avoid adding unsupported details.
- Factory term knowledge now covers relevant production concepts and proper-name handling, including 異型棒, 大成, and 爐號. These are reusable term/context rules, not sentence-specific substitutions.
- The shared quantity frame now captures explicit lot references such as 這批料 even when there is no numeral. The same frame informs the first model prompt and flags translations that drop the lot/batch scope.
- The general factory prompt now separates checking an existing process state from performing a process, system stock-entry records from physical material movement, and pending/completed steps, responsibilities, and destinations.
- Factory translation requests no longer use the fast-quality shortcut. The existing single-provider-call flow remains in place; supported GPT reasoning models use low reasoning effort for this route.
- Translation quality checks now flag an anatomical injury detail, such as “hand,” when the source does not specify it.
- Indonesian work-order validation recognizes urgency/material/workflow modifiers in the noun phrase.

## Validation

The factory translation asset release validator passed. Updated Python files passed `py_compile`. All 15 quantity-semantics tests, 11 unified-policy tests, and 9 factory operational-status tests passed. Direct source and target validation covered all three screenshots and rejected a translation that drops 這批料. The complete offline release-gate runner could not start in the local runtime because `requests`, pytest, and Flask are unavailable.
