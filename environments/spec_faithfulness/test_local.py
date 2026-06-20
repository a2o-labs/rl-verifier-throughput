"""Validate the differential spec-faithfulness reward against a real Verus — no policy model needed.
For the `max` problem, feeds: a FAITHFUL spec (→ 1.0), a too-WEAK spec (→ <1.0, lets a buggy impl pass),
a BROKEN spec that rejects the correct impl (→ 0.0), and an `assume` cheat (→ 0.0).

Run:  VERUS_CMD=verus python test_local.py
      (VERUS_CMD can be any command that runs `verus <file.rs>` — a local binary, container wrapper, etc.)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import spec_faithfulness as S

VERUS = os.environ.get("VERUS_CMD", "verus").split()
MAX = next(p for p in S.PROBLEMS if p["name"] == "max")


async def main():
    env = S.load_environment(verus_cmd=VERUS)
    fnames = {getattr(f, "__name__", "") for f in env.rubric._get_reward_funcs()}
    print(f"env built: {len(list(env.dataset))} problems; funcs={sorted(fnames)}; verus_cmd={VERUS}")

    cases = {
        "faithful": "ensures res >= a, res >= b, res == a || res == b,",
        "too_weak": "ensures res >= a,",
        "broken":   "ensures res == a + b,",
        "cheat":    "ensures res >= a, res >= b,\n    // assume(false)\n",  # 'assume' token -> cheat filter
    }
    results = {}
    for label, spec in cases.items():
        reward, detail = S.faithfulness_reward(MAX, spec, VERUS)
        results[label] = (reward, detail)
        print(f"  {label:9} -> reward={reward:.3f}  {detail}")

    checks = {
        "faithful == 1.0": results["faithful"][0] == 1.0,
        "too_weak in (0,1) (misses >=1 buggy)": 0.0 < results["too_weak"][0] < 1.0,
        "broken == 0.0 (rejects correct impl)": results["broken"][0] == 0.0,
        "cheat == 0.0 (assume blocked)": results["cheat"][0] == 0.0 and results["cheat"][1].get("cheated"),
    }
    for k, v in checks.items():
        print(f"  {'OK ' if v else 'XX '} {k}")
    print("PASS" if all(checks.values()) else "FAIL")
    sys.exit(0 if all(checks.values()) else 1)


asyncio.run(main())
