"""Validate the multi-turn repair mechanics against a live Kimina — no policy model needed.
Drives env_response by hand: a failing attempt must yield Lean-error feedback (solved=False), and the
follow-up reference proof must flip solved=True; a sorryAx attempt must stay unsolved.

Run:  KIMINA_URL=http://<host>:8000 python test_repair.py       # streams the corpus from HF
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
    env = L.load_environment(n=4, kimina_url=KIMINA, proofs_path=PROOFS, deadline=120, repair_turns=1)
    assert isinstance(env, L.LeanRepairEnv), "repair_turns>0 must give a MultiTurnEnv"
    row = list(env.dataset)[0]
    info = json.loads(row["info"])

    # turn 1: a broken attempt -> env must report Lean's error and NOT mark solved
    state = {"info": info}
    msgs = [{"role": "user", "content": "prove it"}, {"role": "assistant", "content": "  exact 42"}]
    resp = await env.env_response(msgs, state)
    fb = resp[0]["content"]
    print(f"turn1 (broken): solved={state['solved']} attempts={state['attempts']}")
    print(f"  feedback head: {fb[:120]!r}")
    t1_ok = (state["solved"] is False) and ("Lean reported" in fb) and ("L" in fb)

    # turn 2: the reference proof -> env must flip solved=True
    msgs += [resp[0], {"role": "assistant", "content": info["reference_body"]}]
    resp2 = await env.env_response(msgs, state)
    print(f"turn2 (reference): solved={state['solved']} attempts={state['attempts']} reply={resp2[0]['content']!r}")
    stop = await env.solved(state)
    t2_ok = (state["solved"] is True) and (stop is True)

    # sorryAx must never count as solved even multi-turn
    s2 = {"info": info}
    await env.env_response([{"role": "user", "content": "x"}, {"role": "assistant", "content": "  exact sorryAx _"}], s2)
    ax_ok = s2["solved"] is False
    print(f"sorryAx attempt: solved={s2['solved']} (must be False)")

    # the reward func reads state['solved']
    funcs = {getattr(f, "__name__", "") for f in env.rubric._get_reward_funcs()}
    rew_ok = {"lean_solved", "repair_attempts", "not_cheating"} <= funcs

    checks = {"turn1 gives Lean-error feedback, unsolved": t1_ok,
              "turn2 reference flips solved + stop": t2_ok,
              "sorryAx never solved": ax_ok,
              "reward funcs registered": rew_ok}
    for k, v in checks.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("PASS ✅" if all(checks.values()) else "FAIL ❌")
    sys.exit(0 if all(checks.values()) else 1)


asyncio.run(main())
