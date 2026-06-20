# spec-faithfulness — a mechanically-checked spec-writing environment for `verifiers`

A PrimeIntellect [`verifiers`](https://github.com/PrimeIntellect-ai/verifiers) environment where the model
writes a **formal specification** (Verus `requires`/`ensures` clauses) for a function, and the reward is a
**differential check against a real [Verus](https://github.com/verus-lang/verus) verifier** — not an LLM
judge, not a string match.

## The reward: a faithful spec accepts the right impl and rejects the wrong ones
```
reward = 0                          if the spec rejects the reference (correct) implementation  (too strong / broken)
       = (# buggy impls rejected) / (# buggy impls)   otherwise
       = 1.0                        iff it also rejects every known-buggy implementation        (faithful)
```
This is the mechanical core of **Verus-SpecGym** ([arXiv 2605.26457](https://arxiv.org/abs/2605.26457)): an
unfaithful spec fails in one of two ways — **too strong** (rejects a correct program) or **too weak** (accepts
a buggy one). Both are caught here with **no expert gold spec and no LLM judge** (the paper reports LLM judges
miss ~26% of the failures a verifier catches). The graded reward (e.g. 1.0 / 0.67 / 0.0) is a clean RL signal.

## Why it's hard to game
- The model supplies **only the spec clauses**; the env fixes the signature and **all** implementations, so
  the policy cannot prove an easier function.
- Vacuous specs are **self-defeating**: `ensures true` / `requires false` let the buggy impls verify too, so
  they reject nothing → low reward. `assume`/`admit`/`external_body`/`no_verify` are blocked outright (and
  tracked by a `not_cheating` monitor).
- The check is real Verus/SMT verification, so a plausible-but-unsound spec scores by what it actually rules out.

## Use
```bash
# 1. a `verus` binary on PATH (https://github.com/verus-lang/verus — prebuilt container or `rustup`+toolchain)
export VERUS_CMD=verus
# 2. install + eval (the policy needs an inference model; the env itself is GPU-free)
prime env install spec-faithfulness
prime eval run spec-faithfulness --model <your-model>
```
`load_environment(verus_cmd=None, timeout=60.0)` — `verus_cmd` defaults to `$VERUS_CMD` split (else `["verus"]`).

## Validate the reward without a policy model
`test_local.py` feeds, for `max`, a faithful spec (→ 1.0), a too-weak spec (→ 0.67, lets a buggy impl pass),
a broken spec that rejects the correct impl (→ 0.0), and an `assume` cheat (→ 0.0) — straight at a real Verus:
```bash
VERUS_CMD=verus python test_local.py    # -> faithful 1.0 / too_weak 0.67 / broken 0.0 / cheat 0.0 ; PASS
```

## Status / limits (v0.1)
- **Seed problem set (16): `max`/`min`/`max3`/`min3`/`clamp`, `is_even`/`is_odd`, `leq`/`geq`/`equal`/
  `not_equal`/`in_range`, `logical_and`/`or`/`xor`/`implies`** — each with a correct impl + 3–4 buggy mutants
  (56 total). Chosen to be clean in Verus (bool / comparison / min-max — no overflow), so a mutant fails for
  the *spec* reason, not an incidental arithmetic error. Every problem is checked well-formed by `gold_check.py`
  (a hand-written faithful spec scores exactly 1.0 — correct verifies + all mutants rejected). **Not yet the
  full Verus-SpecBench (581 Codeforces tasks)** — that integration + auto-mutant-generation is the v0.2.
- Single-turn (no spec-repair loop yet); a `MultiTurnEnv` that feeds the verifier's counterexample/error back
  is the natural follow-up (same pattern as the sibling `lean-verifier` env).
- Pins Verus only by the toolchain you install; record the Verus version for reproducible results.
- Buggy mutants are hand-authored; some incorrect impls may fail for an incidental reason (e.g. arithmetic
  overflow) rather than the spec — kept the seed mutants clean, but auto-generated mutants need that guard.
