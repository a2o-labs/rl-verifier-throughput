# External review of scorecard v0.1 (2026-06-19) + fix status

An adversarial code review of v0.1. Verdict: **the measurement is honest (every number recomputes from
`scorecard_v0.1.json`), but the WRITE-UP repeatedly overstated past the data at exactly the spots that erode
the only moat — neutral-measurer credibility.** Accepted in full. Status below; **v0.2 (re-run) supersedes
the numbers and reframes the headline around the hung-proof tail.**

## High — credibility-eroding
- **H1 — corpus is 100% `set_option maxHeartbeats 0` (undisclosed), and it partly MANUFACTURES Finding 2.**
  All 300 proofs disable Lean's kernel compute limit → some "hangs" are corpus-induced, not intrinsic
  non-termination; and the cheapest lever (a finite maxHeartbeats → hangs fail fast in-kernel, no sandbox
  needed) was missed. → DISCLOSED here; harness gains `--maxheartbeats`; **the finite-vs-0 A/B is P1/v0.2.**
- **H2 — `deploy/kimina-deploy.yaml` shipped MAX_REPLS=7, contradicting the repo's own "≈cores/2" finding.**
  → FIXED: MAX_REPLS=4 (+ comment); cpu limit 7→5.
- **H3 — per-core "same ballpark" used inconsistent denominators** (ours ÷REPLs=4, paper ÷physical-cores=60).
  Same denominator: 0.178/8 = 0.022/core = **~3.2× below** paper; single-REPL 0.058 vs 0.83 = **~14× below**.
  → FIXED in text; this *strengthens* the "commodity throughput is low + saturates early" thesis.
- **H4 — the "v4.26-compatible / version-skew" pass-rate story is unsupported and self-refuted.** Code never
  reads the toolchain version; and `pass_rate == transport_ok` exactly at every C ⇒ zero Lean-error failures
  ⇒ **all misses are timeouts, no version-skew evidence.** → FIXED: reframed as transport-success; v2 harness
  reports `lean_err_rate` separately to make this explicit.
- **H5 — "C=4 beats C=7" is 1.1% single-run noise** (no repeats/CI; ±1 hung proof swings throughput 6–14%).
  The robust saturation signal is LATENCY (p50 4.07→8.05s = 1.98×). → FIXED: saturation led by latency;
  "C=7 worse" softened to "C=4–7 flat, pending repeats."
- **H6 — client timeout 240 > server MAX_WAIT 180 ⇒ p99 pegged at 240 (a constant, not a measured
  percentile); and MAX_WAIT didn't bound running proofs (they hit 240, not 180) ⇒ it gates queue admission,
  not per-proof execution.** Tuning MAX_WAIT per Finding 2 was the wrong knob; the real per-proof kill lever
  is **Lean maxHeartbeats**. → FIXED: client deadline=190 follows server; p99 replaced by `timeout_rate`;
  MAX_WAIT role clarified in manifest + scorecard.

## Medium
- **M1** infotree_type:"original" built an infotree the code never read → throughput included unused work
  (v0.1 numbers are pessimistic). → FIXED: infotree_type=null.
- **M2** v0.1 measured through the pod (cpu limit 7) but framed as "8-core native". → FIXED: manifest cpu
  limit now 5 ≈ MAX_REPLS+1; v0.2 states the pod-cap base explicitly.
- **M3** C-sweep {1,2,4,7} couples client-pipelining with server REPLs and undersamples the knee (no 3/5/6).
  → FIXED: v2 sweeps 1..7; v0.2 will show the full knee.
- **M4** C=1 baseline dragged by the serialized hung tail (~28% of wall) → speedup inflated (~2× real, not
  3.07×). → FIXED: v2 reports `tail_worktime_share` + tail-robust good-proof latency; speedup flagged tail-sensitive.
- **M5** headline "warm 27.2s/cold 33.9s" was proofs[0] (one slow proof); representative warm = p50 ~3.2s.
  → FIXED: relabeled "first/second of one hard proof"; representative warm = p50.
- **M6** "the exact set the paper benchmarked / n=1000" had no in-repo citation. → FIXED: softened to
  "the corpus the Kimina paper reports using (arXiv 2504.21230 Table 1); we run a 100-proof subset."

## Low / polish
- **L1** `srv_err = "error" in r` (key-present) false-fail hazard → `bool(r.get("error"))`. FIXED.
- **L2** MAX_REPL_MEM 8G×7=56G ≫ pod 22Gi (cap never bit). FIXED: 5G×4=20G < 22Gi.
- **L3** cold-probe never restarts the server → bogus "cold" on a warm server. FIXED: prints a warning.
- **L4** default --n 150 vs run 100 vs file 300. FIXED: default 100.
- **L5** `%.2f` hid 0.178 vs 0.176. FIXED: 4-decimal proofs/s.
- **L6** SCORECARD footer linked `the plan doc (dead link). FIXED.
- **L7** no pinned Lean/Mathlib (movable image tag), HF dataset unpinned. → v0.2 records the image's
  `lean-toolchain` + Mathlib commit + the dataset revision.
- **L8** so-what "a real RL-loop bottleneck" from a single N=1 run. FIXED: "consistent with … (single run,
  no repeats)".

## Strategic (the real headline, to lead v0.2)
The buried, data-backed finding is the strongest one: **~5–6% of proofs consume ~72–83% of single-thread
wall; a tuned per-proof deadline (≈30s, not 240s) lifts C=1 from 0.058 to ~0.16 proofs/s — i.e. one thread
with a good timeout ≈ the measured 4-REPL pool. "The lever is killing the tail, not adding cores."** v0.2
turns this from the reviewer's recomputation into our own measured result (per-proof capture + the
finite-maxHeartbeats and deadline A/Bs), and leads the scorecard with it.

*Fixes committed; numbers superseded by v0.2 (re-run). Harness v2 = `verify_throughput.py`.*
