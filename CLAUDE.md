# CLAUDE.md

Project: **hl-whale-signals** — see `IMPLEMENTATION_PLAN.md` (phases, specs) and
`docs/api-notes.md` (verified API facts; these override the plan where they differ).

Run all gates: `scripts/ci.sh`

## 2. Engineering rules (copy into `CLAUDE.md`)

1. **Single home per logic.** Every distinguished piece of logic lives in exactly one
   module (see the Single-Home Registry, Section 5). Other code calls it; it never
   re-implements it. If you need it somewhere new, import it; if the import direction is
   illegal, the logic is in the wrong layer — move it, don't copy it.
2. **No copy-paste**, including in tests. Shared test behavior goes into fixtures,
   factories (`tests/factories.py`), or parametrized contract suites.
3. **GoF patterns only where they solve a named problem** (Section 4). No pattern for its
   own sake. **No Singletons** — all collaborators are injected.
4. **Pure core, I/O at the edge.** `domain`, `core`, `signals`, `wallets/scoring`,
   `wallets/filters`, `session` perform no I/O and read no wall-clock time.
5. **Time is injected.** Only `core/clock.py` knows how to get "now". Nothing calls
   `datetime.now()` / `time.time()` / `time.sleep()` directly.
6. **No magic numbers.** Every threshold, weight, window and floor comes from config
   (`app/config.py` defaults), never literals in logic.
7. **Evidence-only outputs.** Every score carries the raw inputs that produced it.
   No field in the report may be unexplained.
8. **No network in unit tests.** Use `FixtureTransport`. Live tests are marked
   `@pytest.mark.live` and are excluded by default.
9. **Typed everything.** `mypy --strict`. Domain models are frozen dataclasses.
10. **Fail loudly on unknown API shapes.** Unknown enum values / missing required fields
    raise typed errors; they are never silently coerced.

