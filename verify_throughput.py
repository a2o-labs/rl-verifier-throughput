"""verify-deployability harness v2 — Lean verification throughput on commodity on-prem CPU.

Drives the Kimina Lean Server with Goedel-LM/Lean-workbook proofs and measures,
per client-concurrency C: proofs/s, per-proof latency dist, **timeout rate** (the hung-proof tail), and
transport-success. v2 fixes from the 2026-06-19 review:
  - infotree_type=null (don't pay for an infotree we never read)            [M1]
  - client deadline follows server MAX_WAIT (default 190 > server 180)       [H6]
  - srv_err = bool(r.get("error"))  (key-present was a false-fail hazard)    [L1]
  - per-proof latencies + timeout flag captured to <out>.rows.jsonl          [M4/H6]
  - tail_worktime_share reported (what % of REPL work-time the timeouts eat; = wall share only at C=1)  [M4]
  - --maxheartbeats N rewrites the corpus's `set_option maxHeartbeats 0` -> N  [H1: the cheap tail lever]
  - finer concurrency sweep incl. 3/5/6; 3-decimal output; cold-probe warns   [M3/L5/L3]
Server-side parallelism = min(C, LEAN_SERVER_MAX_REPLS); for a clean MAX_REPLS scan, set C >= MAX_REPLS.

Run: python3 verify_throughput.py --base http://<kimina-host>:8000 --n 100 --concurrency 1,2,3,4,5,6,7 \
       [--maxheartbeats 200000] [--deadline 190]
"""
import argparse
import json
import re
import statistics as st
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_HB = re.compile(r"set_option\s+maxHeartbeats\s+\d+")


def prep(proof, maxhb):
    if maxhb is None:
        return proof
    if _HB.search(proof):
        return _HB.sub(f"set_option maxHeartbeats {maxhb}", proof)
    return f"set_option maxHeartbeats {maxhb}\n" + proof


def verify(base, proof, deadline):
    # M1: infotree_type=None (-> JSON null) confirmed to DISABLE infotree build server-side (Kimina 2.0.0):
    # response keys drop to ['messages','time'] (no 'infotree') and latency is lower. [review #2 verified]
    body = json.dumps({"codes": [{"custom_id": "p", "proof": proof}], "infotree_type": None}).encode()
    t = time.time()
    try:
        req = urllib.request.Request(base + "/verify", body, {"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=deadline).read())
        r = d["results"][0].get("response", {})
        msgs = r.get("messages", []) if isinstance(r, dict) else []
        lean_err = any(isinstance(m, dict) and m.get("severity") == "error" for m in msgs)
        srv_err = (not isinstance(r, dict)) or bool(r.get("error"))  # L1
        return {"lat": time.time() - t, "passed": (not lean_err and not srv_err),
                "lean_err": lean_err, "ok": True, "timed_out": False}
    except Exception as e:
        dt = time.time() - t
        return {"lat": dt, "passed": False, "lean_err": False, "ok": False,
                "timed_out": dt >= deadline - 5, "err": str(e)[:60]}


def run_level(base, proofs, C, deadline, fout=None):
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=C) as ex:
        res = list(ex.map(lambda p: verify(base, p, deadline), proofs))
    wall = time.time() - t0
    lats = [r["lat"] for r in res]
    n_to = sum(r["timed_out"] for r in res)
    n_lean_err = sum(r["lean_err"] for r in res)
    # tail-robust throughput: time spent on NON-timed-out proofs only / their count
    good = [r["lat"] for r in res if not r["timed_out"]]
    # share of total verification WORK-TIME (REPL-seconds, = Σlat) eaten by the timeout tail.
    # NOTE: only equals wall-clock share at C=1 (single thread); at C>1 it's work-time, not wall. [review #1]
    tail_worktime_share = round(sum(r["lat"] for r in res if r["timed_out"]) / max(1e-9, sum(lats)), 3)
    if fout:
        for r in res:
            fout.write(json.dumps({"C": C, "lat": round(r["lat"], 2), "to": r["timed_out"],
                                   "lean_err": r["lean_err"], "ok": r["ok"]}) + "\n")
    return {"C": C, "n": len(proofs), "wall_s": round(wall, 1),
            "proofs_per_s": round(len(proofs) / wall, 4),
            "lat_p50": round(st.median(lats), 2),
            "lat_mean": round(st.mean(lats), 2),
            "good_lat_mean": round(st.mean(good), 2) if good else None,
            "timeout_rate": round(n_to / len(res), 3),
            "lean_err_rate": round(n_lean_err / len(res), 3),
            "tail_worktime_share": tail_worktime_share}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--proofs", default="proofs.jsonl")
    ap.add_argument("--n", type=int, default=100)                 # L4: match what we actually publish
    ap.add_argument("--concurrency", default="1,2,3,4,5,6,7")     # M3: sample the knee (3,5,6)
    ap.add_argument("--deadline", type=int, default=190)          # H6: follow server MAX_WAIT
    ap.add_argument("--maxheartbeats", type=int, default=None)    # H1: None=as-corpus(0); finite=the lever
    ap.add_argument("--tag", default="v0.x")
    a = ap.parse_args()
    raw = [json.loads(l)["proof"] for l in open(a.proofs)][:a.n]
    proofs = [prep(p, a.maxheartbeats) for p in raw]
    levels = [int(x) for x in a.concurrency.split(",")]
    mh = "as-corpus(0)" if a.maxheartbeats is None else str(a.maxheartbeats)
    print(f"tag={a.tag} n={len(proofs)} deadline={a.deadline}s maxHeartbeats={mh}")
    print("cold-start probe (NOTE: only meaningful right after a FRESH deploy; on a warm server this is not cold) [L3]")
    cold = verify(a.base, proofs[0], a.deadline); warm = verify(a.base, proofs[0], a.deadline)
    print(f"  proofs[0]: first={cold['lat']:.1f}s second={warm['lat']:.1f}s (single hard proof; representative warm = p50 below) [M5]")

    rows = []
    rf = open(f"{a.tag}.rows.jsonl", "w")
    for C in levels:
        print(f"running C={C} ...", flush=True)
        r = run_level(a.base, proofs, C, a.deadline, rf); rows.append(r)
        print(f"  C={C}: {r['proofs_per_s']:.4f} proofs/s | p50={r['lat_p50']}s good_mean={r['good_lat_mean']}s | "
              f"timeout_rate={r['timeout_rate']} lean_err={r['lean_err_rate']} tail_worktime_share={r['tail_worktime_share']} | wall={r['wall_s']}s", flush=True)
    rf.close()

    base1 = next((r["proofs_per_s"] for r in rows if r["C"] == 1), rows[0]["proofs_per_s"])
    print(f"\n=== scorecard {a.tag} — Kimina/Lean (corpus maxHeartbeats={mh}, deadline={a.deadline}s) ===")
    print(f"{'C':>3}{'proofs/s':>11}{'speedup':>9}{'p50_s':>8}{'to_rate':>9}{'tail_wt':>10}")
    for r in rows:
        print(f"{r['C']:>3}{r['proofs_per_s']:>11.4f}{r['proofs_per_s']/base1:>9.2f}{r['lat_p50']:>8.1f}"
              f"{r['timeout_rate']:>9.3f}{r['tail_worktime_share']:>10.3f}")
    json.dump({"tag": a.tag, "n": a.n, "deadline_s": a.deadline, "maxheartbeats": mh,
               "cold_first_s": round(cold["lat"], 1), "warm_second_s": round(warm["lat"], 1),
               "levels": rows}, open(f"{a.tag}.json", "w"), indent=1)
    print(f"\n-> {a.tag}.json + {a.tag}.rows.jsonl (per-proof). Paper 60-core 0.83->4.33 proofs/s; ours = commodity end.")


if __name__ == "__main__":
    main()
