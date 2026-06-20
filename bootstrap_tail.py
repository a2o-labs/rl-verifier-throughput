"""P1 tail-rate CI — bootstrap + disjoint-block analysis over a captured per-proof latency run.

Answers the open question on scorecard v0.2: "the n=100 tail-rate (4%) is the one noisy quantity —
how much does it actually swing?" Uses one large as-corpus run (v0.3-tail1k.rows.jsonl) and:
  (a) the n=1000 point estimate of timeout_rate + serial throughput + the 30s-deadline lift,
  (b) 10 DISJOINT blocks of 100 (real different samples) -> the empirical spread of the n=100 tail-rate,
  (c) a bootstrap (resample proofs with replacement) -> 95% CI on the rate at n=100 AND n=1000.
No new server load — pure arithmetic over the captured latencies. Deterministic seed (no Math.random equiv;
we use a fixed-seed PRNG so the CI reproduces).

Run: python3 bootstrap_tail.py v0.3-tail1k.rows.jsonl [--deadline 190] [--tailsim 30]
"""
import argparse
import json
import random
import statistics as st


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def throughput(lats, cap=None):
    """serial proofs/s; cap = per-proof deadline applied post-hoc (None = as-run)."""
    if cap is not None:
        lats = [min(l, cap) for l in lats]
    s = sum(lats)
    return len(lats) / s if s else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rows")
    ap.add_argument("--deadline", type=int, default=190)   # how the run defined a timeout
    ap.add_argument("--tailsim", type=int, default=30)      # the post-hoc deadline to simulate
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--block", type=int, default=100)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.rows)]
    lats = [r["lat"] for r in rows]
    to = [bool(r["to"]) for r in rows]                      # timed_out flag as captured
    N = len(rows)
    rng = random.Random(20260620)                          # fixed seed -> reproducible CI

    # (a) n=N point estimates
    rate_N = sum(to) / N
    thr_N = throughput(lats)
    thr_sim = throughput(lats, a.tailsim)
    tail_share = sum(l for l, t in zip(lats, to) if t) / sum(lats)
    print(f"=== n={N} point estimates (as-corpus, run deadline={a.deadline}s) ===")
    print(f"  timeout_rate          = {rate_N*100:.2f}%  ({sum(to)}/{N})")
    print(f"  serial throughput     = {thr_N:.4f} proofs/s")
    print(f"  tail_worktime_share   = {tail_share*100:.1f}%")
    print(f"  throughput @ {a.tailsim}s deadline (sim) = {thr_sim:.4f} proofs/s  ({thr_sim/thr_N:.2f}x)")
    print(f"  good_lat: p50={pct([l for l,t in zip(lats,to) if not t],0.5):.2f}s "
          f"mean={st.mean([l for l,t in zip(lats,to) if not t]):.2f}s")

    # (b) disjoint blocks of `block` -> REAL different-sample spread of the n=block tail-rate
    nb = N // a.block
    block_rates = [sum(to[i*a.block:(i+1)*a.block]) / a.block for i in range(nb)]
    block_thr = [throughput(lats[i*a.block:(i+1)*a.block]) for i in range(nb)]
    print(f"\n=== {nb} disjoint blocks of {a.block} (real different samples) ===")
    print(f"  block timeout_rates : {[f'{r*100:.0f}%' for r in block_rates]}")
    print(f"  -> mean {st.mean(block_rates)*100:.1f}%  range [{min(block_rates)*100:.0f}%, {max(block_rates)*100:.0f}%]"
          f"  sd {st.pstdev(block_rates)*100:.1f}pp")
    print(f"  block throughput    : range [{min(block_thr):.3f}, {max(block_thr):.3f}] proofs/s "
          f"(swing {max(block_thr)/min(block_thr):.1f}x)")

    # (c) bootstrap: resample N proofs w/ replacement -> CI on the rate at n=N and n=block
    def boot(n):
        rates, thrs, sims = [], [], []
        for _ in range(a.draws):
            idx = [rng.randrange(N) for _ in range(n)]
            s_to = [to[i] for i in idx]
            s_lat = [lats[i] for i in idx]
            rates.append(sum(s_to) / n)
            thrs.append(throughput(s_lat))
            sims.append(throughput(s_lat, a.tailsim))
        return rates, thrs, sims
    for n in (N, a.block):
        rates, thrs, sims = boot(n)
        print(f"\n=== bootstrap 95% CI at n={n} ({a.draws} draws) ===")
        print(f"  timeout_rate : {st.mean(rates)*100:.2f}%  [{pct(rates,.025)*100:.2f}%, {pct(rates,.975)*100:.2f}%]")
        print(f"  throughput   : {st.mean(thrs):.4f}  [{pct(thrs,.025):.4f}, {pct(thrs,.975):.4f}] proofs/s")
        print(f"  thr @ {a.tailsim}s   : {st.mean(sims):.4f}  [{pct(sims,.025):.4f}, {pct(sims,.975):.4f}] proofs/s")


if __name__ == "__main__":
    main()
