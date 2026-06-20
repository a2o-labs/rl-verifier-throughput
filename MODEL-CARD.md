# Verifier-grounded RL environments for on-prem AI4Math — a unified card

Two PrimeIntellect [`verifiers`](https://github.com/PrimeIntellect-ai/verifiers) environments whose reward is
a **real formal verifier**, not an LLM judge and not a string match — built to run **on commodity, on-prem,
GPU-free** hardware. They share one thesis: as *RL-with-verifiers* scales, the verifier is simultaneously the
**reward source** and the **throughput bottleneck**, so the leverage is in the *verifier infrastructure* — and
that infrastructure should be **verifier-agnostic** (Lean today, Verus today, Rocq/Z3 tomorrow).

| Env | Verifier | The model produces | Reward |
|---|---|---|---|
| [`kit-kyo/lean-verifier`](https://app.primeintellect.ai/dashboard/environments/kit-kyo/lean-verifier) | Lean 4 / Kimina (Mathlib) | a **proof** (tactic block) | 1.0 iff Lean accepts it, no `sorry`/`sorryAx` |
| [`kit-kyo/spec-faithfulness`](https://app.primeintellect.ai/dashboard/environments/kit-kyo/spec-faithfulness) | Verus (Rust, SMT/Z3) | a **specification** (`requires`/`ensures`) | fraction of buggy impls the spec rejects (0 if it rejects the correct one) |

## Shared design principles
1. **The reward is a real verifier.** A proof either type-checks or it doesn't; a spec either rules out a
   buggy program or it doesn't. No approximate judge in the loop.
2. **Hard to game, by construction.** The model only ever supplies the *verifiable* artifact; the surrounding
   context is fixed by the env, so it can't prove an easier thing:
   - `lean-verifier` fixes the theorem statement and re-attaches it server-side; `sorry`/`sorryAx` are caught
     by a **structured** check on Lean's `declaration uses 'sorry'` message (a severity-only reward would
     accept `exact sorryAx _` for any goal — a real reward-hack this env closes).
   - `spec-faithfulness` fixes the signature and *all* implementations; a vacuous spec (`ensures true`,
     `requires false`) is **self-defeating** — it lets the buggy impls verify too, so it rejects nothing and
     scores low. `assume`/`admit`/`external_body` are blocked.
3. **Defaults come from a deployability study, not folklore** (see below).
4. **Repair is first-class** where it helps: `lean-verifier` has a `repair_turns` mode that feeds the
   verifier's real diagnostics back for a fix step (the natural RL signal for a repair-capable policy).

## The deployability study behind the defaults
The same work measured the verifier as a *deployment* on an 8-core commodity box (the on-prem end of the
curve), and those numbers set the env defaults — full write-up:
[*Kill the tail, not the cores*](https://blog.sagamiyun.me/blog/kill-the-tail-not-cores/).
- **A small tail dominates.** ~**4.5 % of proofs** (95 % CI **[3.2 %, 5.8 %]**, n=1000) run away and would each
  consume a full timeout per rollout — murder for an RL loop. So the envs default to a **finite Lean
  `maxHeartbeats`** and a **per-proof deadline**.
- **Two cheap levers, both measured.** A 30 s per-proof deadline lifts single-thread throughput **2.3×**; a
  finite `maxHeartbeats` roughly **doubles peak** throughput by killing the tail (at a small, disclosed
  cost: ~3 pp of legitimately-expensive proofs are rejected). *Kill the tail, not add cores.*
- **Saturate early.** On 8 cores, throughput plateaus at ~4–6 parallel REPLs and *regresses* at 7 — so the
  deployable rule is `MAX_REPLS ≈ cores/2–¾`, never `= cores`.

## Use
```bash
prime env install kit-kyo/lean-verifier        # needs a Kimina Lean Server (KIMINA_URL)
prime env install kit-kyo/spec-faithfulness     # needs a verus binary (VERUS_CMD)
prime eval run kit-kyo/lean-verifier --model <your-model>
```
Each env is GPU-free (the verifier is CPU/SMT-bound); the policy model is separate. Both ship a
`test_local.py` that validates the reward against a live verifier **without any policy model** — feeding
reference-correct, deliberately-broken, and cheat inputs and asserting the scores.

## Honest limits
- `lean-verifier`: tactic-mode proofs only; expects a well-indented tactic block; Lean/Mathlib pinned by a
  movable image tag.
- `spec-faithfulness`: a **16-problem seed set** (56 hand-authored mutants), all gold-validated against real
  Verus — *not yet* the full Verus-SpecBench (581 Codeforces tasks); that + auto-mutant-generation is next.
- Single backbone, single-run scorecard numbers (the tail-rate is the CI'd quantity); see each env's README.

*These environments are the applied half of a verify-throughput / deployability study; the scorecard measures
how fast on-prem verification runs, and these envs are that verifier wired up as an RL reward.*
