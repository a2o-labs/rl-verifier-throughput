"""Local validation of the lean_verifier reward against a real Kimina server — no policy model needed.
Feeds (a) the reference proof -> expect 1.0, (b) `sorry` -> expect 0.0 (cheat), (c) a broken proof -> 0.0,
and checks the env builds with both rubric funcs registered.

Run:  KIMINA_URL=http://<host>:8000 python test_local.py        # streams the corpus from HF
      KIMINA_URL=http://<host>:8000 PROOFS=local.jsonl python test_local.py   # or use a local JSONL
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import lean_verifier as L

KIMINA = os.environ.get("KIMINA_URL", "http://localhost:8000")
PROOFS = os.environ.get("PROOFS")  # None -> stream Goedel-LM/Lean-workbook-proofs from HF


async def main():
    env = L.load_environment(n=6, kimina_url=KIMINA, proofs_path=PROOFS, deadline=120)
    rows = list(env.dataset)
    fnames = {getattr(f, "__name__", str(f)) for f in env.rubric._get_reward_funcs()}
    print(f"dataset built: {len(rows)} rows; registered reward funcs={sorted(fnames)}; kimina={KIMINA}")
    assert {"lean_verified", "not_cheating"} <= fnames, f"reward funcs not registered: {fnames}"

    npass = 0
    for row in rows[:4]:
        info = json.loads(row["info"])
        r, detail = await L.verify_reward(info, info["reference_body"], KIMINA, 120)
        print(f"  [{info['problem_id']:>22}] reference -> reward={r}  {detail}")
        npass += int(r == 1.0)

    info0 = json.loads(rows[0]["info"])
    r_sorry, d_sorry = await L.verify_reward(info0, "  sorry", KIMINA, 120)
    print(f"  sorry        -> reward={r_sorry}  {d_sorry}")
    # the reward-hack the review caught: sorryAx slips the regex but Lean flags it -> must be rejected
    r_ax, d_ax = await L.verify_reward(info0, "  exact sorryAx _", KIMINA, 120)
    print(f"  sorryAx hack -> reward={r_ax}  {d_ax}")
    r_bad, d_bad = await L.verify_reward(info0, "  exact 42", KIMINA, 120)
    print(f"  exact 42     -> reward={r_bad}  {d_bad}")
    # a comment that merely mentions sorry must NOT be a false cheat hit (it should reach Lean and pass)
    ref_with_comment = info0["reference_body"].rstrip() + "\n  -- not a sorry, just a note\n"
    r_cmt, d_cmt = await L.verify_reward(info0, ref_with_comment, KIMINA, 120)
    print(f"  ref+comment  -> reward={r_cmt}  {d_cmt}  (must NOT be cheat-flagged)")
    # LOAD-BEARING: prove the STRUCTURED gate fires, not just the regex. Call _kimina_verify directly
    # (bypassing the Layer-1 regex) on a sorryAx proof -> Kimina must return the warning and the gate must
    # classify it `sorry_axiom` (NOT a pass, NOT lean_error). If Kimina ever stopped emitting the warning,
    # this fails loudly instead of silently letting sorryAx through.
    full_ax = L.build_full_code(info0, "exact sorryAx _")
    ok_ax, why_ax, _ = await L._kimina_verify(KIMINA, full_ax, 120)
    print(f"  _kimina_verify(sorryAx) -> verified={ok_ax} why={why_ax!r}  (structured gate, regex bypassed)")

    checks = {
        "refs verified (>=3/4)": npass >= 3,
        "plain sorry -> 0": r_sorry == 0.0 and d_sorry["cheated"],
        "sorryAx hack -> 0 (FIX)": r_ax == 0.0,
        "broken -> 0": r_bad == 0.0,
        "comment-with-sorry not false-rejected": (r_cmt == 1.0) and (not d_cmt.get("cheated")),
        "structured gate fires (Kimina warning -> sorry_axiom)": (ok_ax is False) and (why_ax == "sorry_axiom"),
    }
    for k, v in checks.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("PASS ✅" if all(checks.values()) else "FAIL ❌")
    sys.exit(0 if all(checks.values()) else 1)


asyncio.run(main())
