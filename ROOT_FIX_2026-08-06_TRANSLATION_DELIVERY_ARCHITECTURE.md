# Translation Delivery Architecture Root Fix — 2026-08-06

## Contract

This change replaces sentence-by-sentence exception patches with a delivery architecture:

1. **Persist before work.** Text, image, audio, video and supported document requests are written to a SQLite outbox before provider calls or media processing.
2. **Deliver or remain pending.** A job is removed only after LINE accepts the translation. Empty provider output, timeout, deploy or process crash leaves the source job pending.
3. **No terminal retry count.** Retries use bounded exponential backoff but have no exhausted state.
4. **Crash and multi-worker safety.** Atomic SQLite leases prevent concurrent Gunicorn workers from processing the same job. Expired leases are reclaimed after a worker dies.
5. **Stable delivery idempotency.** Immediate and delayed delivery use a deterministic LINE retry key based on the source job.
6. **Unknown-language routing.** Detection uncertainty becomes `auto`; it no longer causes a message to be discarded.
7. **Provider independence.** Emergency routing tries a locally provisioned translator, configured NMT and a public NMT fallback. Argos Translate can provide an offline route when its language packages are installed.
8. **Validation cannot become a warning message.** Quality checks control ranking, retries and cache/TM admission. They cannot replace a real provider translation with “cannot translate” status text.
9. **Failure status is not conversation content.** Operational errors are logged only. The conversation receives the actual translation when delivery succeeds.
10. **Media source preservation.** Once OCR/STT/document extraction succeeds, the extracted source is persisted as a text job so LINE media retention cannot erase it.

## Exact limitation

No software can produce an immediate correct translation while every configured online and local translation engine is unavailable, or when the input contains no recoverable text/speech. Under those conditions this build does not fabricate a translation or send a “cannot translate” warning. It keeps a recoverable job pending and delivers after a provider becomes available. Deterministically non-text or silent media is closed without presenting an operational warning as a translation.

## Deployment

Use persistent storage for `TRANSLATION_RETRY_DB_PATH` (for example `/var/data/translation_retry_queue.db`). For provider-independent operation, configure `LOCAL_TRANSLATE_URL` or install Argos Translate plus the required offline language packages. The application does not download model packages at runtime.
