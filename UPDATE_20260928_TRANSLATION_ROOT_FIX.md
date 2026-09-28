# Factory translation source-alignment update

Replace the matching files in the existing project root with the files in this archive. The archive contains only changed files.

## What changed

- Factory-language translations now use source-alignment reasoning: preserve who/what a modifier applies to, distinguish stated facts from inference, and avoid adding unsupported details.
- Factory term knowledge now covers relevant production concepts and proper-name handling, including 異型棒, 大成, and 爐號. These are reusable term/context rules, not sentence-specific substitutions.
- Factory translation requests no longer use the fast-quality shortcut. The existing single-provider-call flow remains in place; supported GPT reasoning models use low reasoning effort for this route.
- Translation quality checks now flag an anatomical injury detail, such as “hand,” when the source does not specify it.
- Indonesian work-order validation recognizes urgency/material/workflow modifiers in the noun phrase.

## Validation

The factory translation asset release validator passed. Updated Python files passed `py_compile`. Direct semantic checks covered terminology, urgency modifier scope, and injury anatomy inference; all 11 tests in `test_unified_factory_translation_root_fix.py` passed. Two existing release-gate assertions were updated to account for the new customer-name knowledge card and explicit wording in the general prompt. The complete offline release-gate runner could not start in the local runtime because `requests`, pytest, and Flask are unavailable.
