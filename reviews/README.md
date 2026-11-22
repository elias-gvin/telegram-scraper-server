# reviews/ — historical documents

**Nothing in this folder is an instruction or a spec.** These files are a record of
how the project was reviewed and rewritten. Source of truth for the current code:
the code itself, `README.md` and `TELEGRAM_SCRAPER_SKILL.md`.

They describe two different codebases:

| Code | Where |
|---|---|
| **Old**: date-range cache, per-dialog SQLModel DBs, `X-Telegram-Username` header, `/api/v3` | commit `4cbade3` and earlier |
| **New**: id-based archive (`archive.py`, `sync.py`, `serialize.py`), bearer token, `/api/v4` | branch `rewrite-archive` (commits after `4cbade3`) |

| File (date = when it was written) | About | Status |
|---|---|---|
| `2026-09-09_prior-1_code-review_earlier-model.md` | Review of the **old** code by an earlier model | Superseded; most findings fixed by the rewrite |
| `2026-09-09_prior-2_independent-technical-review_earlier-model.md` | Second review of the **old** code by an earlier model | Superseded, same as above |
| `2026-11-22_opus-5.5_review_with-live-export-diff.md` | Review of the **old** code + live comparison with Telegram's native export; appendix checks the two reviews above | Superseded; it was the input for the plan |
| `2026-11-22_export-fidelity_tool-vs-native.md` | Field-by-field export comparison: **old** code first, then the **new** code ("After the fix") | Partial test: only five private chats, no large channels/forums |
| `2026-11-22_rewrite-plan.md` | The rewrite plan and the user's decisions | Implemented; section 9 lists deviations |

Section numbers like "§2.1" inside these files refer to sections of the same file.
