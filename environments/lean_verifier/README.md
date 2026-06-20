# lean-verifier — an anti-gameable Lean 4 proving environment for `verifiers`

A PrimeIntellect [`verifiers`](https://github.com/PrimeIntellect-ai/verifiers) environment where the reward is
a **real Lean 4 proof check by a Kimina Lean Server** (Mathlib) — not a string match, not an LLM judge. Its
defaults are tuned from an on-prem verify-throughput study of this same setup: on commodity CPU the verifier
is the bottleneck, and a finite `maxHeartbeats` + a per-proof `deadline` are what keep an RL rollout loop's
verification throughput sane.

## Reward (what it actually checks)
`reward = 1.0` iff the model's proof, reconstructed onto the **fixed** problem header + theorem statement, is
accepted by Lean with **no error-severity messages** — and contains no `sorry` / `admit` / empty escape hatch.

**Why it's hard to game:**
- The model supplies **only the tactic block**; the env re-attaches the *original* `theorem … := by` server-side,
  so the policy cannot quietly prove an easier statement.
- `sorry` / `admit` / empty are rejected **before** Lean runs (and tracked by a `not_cheating` monitor metric).
- The check is a real kernel-level verification, so a "plausible-looking" proof that doesn't compile scores 0.

## Deployable defaults come from the scorecard
The corpus ships `set_option maxHeartbeats 0` (Lean's compute limit disabled). Measured on commodity on-prem
CPU: **~4.5 % of proofs (95 % CI [3.2 %, 5.8 %], n=1000) are runaway and would each eat a full client timeout
per rollout** — murder for an RL loop. So this env rewrites the header to a **finite `max_heartbeats` (default
200 000)** and applies a per-proof `deadline` (default 60 s). Those two knobs are the throughput levers the
scorecard isolated; keep them finite for training. Full write-up + the bootstrap CI:
[*Kill the tail, not the cores*](https://blog.sagamiyun.me/blog/kill-the-tail-not-cores/).

## Use
```bash
# 1. a reachable Kimina Lean Server (prebuilt, Mathlib baked in — no multi-hour build):
docker run -p 8000:8000 projectnumina/kimina-lean-server:2.0.0      # prebuilt; or run it on k8s
export KIMINA_URL=http://localhost:8000

# 2. install + eval (needs an inference model for the policy; the env itself is GPU-free)
prime env install lean-verifier
prime eval run lean-verifier --model <your-model> --num-examples 20
```

`load_environment` kwargs: `n` (problems), `kimina_url`, `max_heartbeats` (0 = unlimited; keep finite for RL),
`deadline` (s), `dataset_name` (default `Goedel-LM/Lean-workbook-proofs`), `proofs_path` (local JSONL of
`{problem_id|id, full_proof|proof}` to use offline instead of streaming HF), **`repair_turns`** (see below).

## Single-shot vs. proof-repair (multi-turn)
- `repair_turns=0` (default) → **`SingleTurnEnv`**: one attempt, reward = verified-or-not.
- `repair_turns=k` → **`MultiTurnEnv`**: on a failed attempt the env feeds **Lean's actual diagnostics** back
  (`L7 error: numerals are data in Lean, but the expected type is a proposition …`) and lets the model repair,
  up to `k` times (total turns `1+k`). Reward = 1.0 if **ever** verified; a `repair_attempts` monitor tracks
  how many turns it took. Verification runs once per turn (≤ `1+k` Kimina calls — no double-verify). The
  `sorry`/`sorryAx` gate holds across turns. This mirrors how real provers work (propose → read the error →
  fix) and is the natural RL signal for training a repair-capable policy.

## Validate the reward without a policy model
`test_local.py` feeds the *reference* proofs (→ 1.0), a `sorry` (→ 0.0, cheat-flagged), and a broken proof
(→ 0.0, real Lean error) straight at a live Kimina — confirming the reward, the anti-cheat, and the
statement-reconstruction end-to-end:
```bash
KIMINA_URL=http://<host>:8000 python test_local.py    # streams the corpus from HF
# -> 4/4 reference verified; sorry->0 & cheat-flagged; broken->0 ; PASS
# test_repair.py does the same for the multi-turn repair mechanics.
```

## Anti-cheat — how `sorry` is actually blocked
Two layers, because a naive string filter is gameable (e.g. `exact sorryAx _` slips a `(sorry|admit)` regex
yet only emits a *warning*, so a severity-only reward would accept it for any goal):
1. **Pre-filter** (cheap, pre-Lean): the whole sorry family + admit — `\b(sorry\w*|admit)\b`, applied after
   **stripping Lean comments** so `exact? -- sorry` is not a false hit.
2. **Structural gate** (the real one): reject if Lean's own messages contain **`declaration uses 'sorry'`**
   (severity *warning*) — this catches `sorry`, `sorryAx`, and anything that desugars to `sorryAx`, regardless
   of the source string. Verified by `test_local.py` (the `sorryAx hack` case scores 0.0).

## Known limits (v0.1)
- **Tactic-mode only:** term-mode proofs (`:= <term>` with no `by`, e.g. `:= rfl`) are skipped; the count is
  printed on load (`built M/N; skipped K term-mode/unparseable`). A term-mode variant is future work.
- Expects a **properly-indented** tactic block (standard for FTP continuation envs); a policy that emits
  unindented multi-line tactics may need a normalizer.
- Proof-repair is available via `repair_turns` (above); the policy still needs to emit a well-formed tactic
  block each turn (no structured-edit interface yet).
- Pins Lean/Mathlib only via the movable `:2.0.0` image tag — record the in-image `lean-toolchain` + Mathlib
  commit before publishing results.
- `native_decide` and other *unsound-by-misuse* tactics are not specifically blocked (the sorry family + admit
  are); add to the cheat filter / message check if your threat model needs it.
