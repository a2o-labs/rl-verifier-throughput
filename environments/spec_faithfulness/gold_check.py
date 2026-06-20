"""Well-formedness check for the seed problem set: for each problem, a hand-written GOLD faithful spec must
score exactly 1.0 (correct impl verifies AND every buggy mutant is rejected). If any problem scores < 1.0 the
problem is mis-constructed (bad spec syntax, a mutant that doesn't actually fail, or an overflow artifact).
These gold specs live ONLY here (not shipped) so they can't leak into the prompt.

Run:  VERUS_CMD=verus python gold_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import spec_faithfulness as S

VERUS = os.environ.get("VERUS_CMD", "verus").split()

GOLD = {
    "max": "ensures res >= a, res >= b, res == a || res == b,",
    "min": "ensures res <= a, res <= b, res == a || res == b,",
    "max3": "ensures res >= a, res >= b, res >= c, res == a || res == b || res == c,",
    "min3": "ensures res <= a, res <= b, res <= c, res == a || res == b || res == c,",
    "clamp": "requires lo <= hi, ensures lo <= res, res <= hi, "
             "(x < lo) ==> (res == lo), (x > hi) ==> (res == hi), (lo <= x && x <= hi) ==> (res == x),",
    "is_even": "ensures res <==> (n % 2 == 0),",
    "is_odd": "ensures res <==> (n % 2 == 1),",
    "leq": "ensures res <==> (a <= b),",
    "geq": "ensures res <==> (a >= b),",
    "equal": "ensures res <==> (a == b),",
    "not_equal": "ensures res <==> (a != b),",
    "in_range": "ensures res <==> (lo <= x && x <= hi),",
    "logical_and": "ensures res <==> (a && b),",
    "logical_or": "ensures res <==> (a || b),",
    "logical_xor": "ensures res <==> (a != b),",
    "logical_implies": "ensures res <==> (a ==> b),",
}


def main():
    by_name = {p["name"]: p for p in S.PROBLEMS}
    assert set(GOLD) == set(by_name), f"GOLD/PROBLEMS mismatch: {set(GOLD) ^ set(by_name)}"
    bad = []
    for name, problem in by_name.items():
        reward, detail = S.faithfulness_reward(problem, GOLD[name], VERUS)
        ok = reward == 1.0
        print(f"  {'OK ' if ok else 'XX '} {name:16} reward={reward:.3f} {detail}")
        if not ok:
            bad.append(name)
    print(f"\n{len(by_name)-len(bad)}/{len(by_name)} problems well-formed.")
    print("PASS" if not bad else f"FAIL: {bad}")
    sys.exit(0 if not bad else 1)


main()
