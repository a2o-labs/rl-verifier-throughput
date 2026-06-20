"""lean-verifier — a faithful, anti-gameable Lean 4 theorem-proving environment for PrimeIntellect `verifiers`.

The reward is a REAL proof check by a Kimina Lean Server (Mathlib), not a string/LLM-judge heuristic:
  reward = 1.0  iff  the model's proof, reconstructed onto the FIXED problem header+statement, is accepted by
                     Lean with no error-severity messages — AND contains no `sorry`/`admit` escape hatch.

Why "anti-gameable": the model only supplies the tactic block; the theorem statement is fixed by the env and
re-attached server-side, so the policy cannot prove an easier theorem, and `sorry`/`admit`/empty are rejected
before Lean ever runs. The verifier is the bottleneck, not a soft signal.

Deployable defaults come from an on-prem verify-throughput study of this exact setup: a FINITE `maxHeartbeats`
(the corpus ships `maxHeartbeats 0`, which lets a runaway proof eat a full timeout per rollout — measured:
~4.5% of proofs, 95% CI [3.2%, 5.8%], consume the bulk of single-thread wall) plus a per-proof client
`deadline`. These are the levers that keep an RL rollout loop's verify throughput sane on commodity CPU.

Requires a reachable Kimina Lean Server (`projectnumina/kimina-lean-server:2.0.0`): set KIMINA_URL or pass
`kimina_url=`. See the repo README for the one-line k3s/docker deploy.
"""
import json
import os
import re
import sys

import httpx
import verifiers as vf
from datasets import Dataset, load_dataset

_HB = re.compile(r"set_option\s+maxHeartbeats\s+\d+")
_FENCE = re.compile(r"```(?:lean4?|)\s*(.*?)```", re.DOTALL)
# Whole sorry FAMILY (sorry, sorryAx, sorry_…) + admit. This is only a cheap PRE-filter that saves a Kimina
# call on obvious cheats; the real gate is the structured "declaration uses 'sorry'" check in _kimina_verify
# (which catches anything that desugars to sorryAx, even if it slips past this regex).
_CHEAT = re.compile(r"\b(sorry\w*|admit)\b")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/-.*?-/", re.DOTALL)


def _strip_comments(s: str) -> str:
    """Drop Lean comments so a comment that merely mentions `sorry` (e.g. `exact? -- sorry`) is not a false
    cheat hit. Block comments first (they can span the line-comment marker)."""
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", s))


def _is_cheat(proof: str) -> bool:
    return bool(_CHEAT.search(_strip_comments(proof)))


def _clean(content: str) -> str:
    """Pull the tactic block out of a model message: take fenced code if present, trim surrounding blank
    lines, but PRESERVE leading indentation (Lean tactic blocks are whitespace-sensitive)."""
    m = _FENCE.search(content)
    if m:
        content = m.group(1)
    return content.strip("\n").rstrip()


def _split(full: str):
    """full_proof -> (header, statement_through_:=by, reference_body). None if it doesn't parse.
    NOTE: this env covers TACTIC-mode proofs (`:= by …`) only; term-mode proofs (`:= <term>`, e.g. `:= rfl`
    with no `by`) return None and are skipped + counted (disclosed on load). A term-mode variant is future
    work — the "return only the tactic block" prompt doesn't fit a bare term."""
    ti = full.find("theorem ")
    if ti < 0:
        ti = full.find("lemma ")
    if ti < 0:
        return None
    bi = full.find(":= by", ti)
    if bi < 0:
        return None
    return full[:ti], full[ti:bi + 5], full[bi + 5:]


def _finite_header(header: str, maxhb: int) -> str:
    if _HB.search(header):
        return _HB.sub(f"set_option maxHeartbeats {maxhb}", header)
    return f"set_option maxHeartbeats {maxhb}\n" + header


def _rows(source, n, maxhb):
    """Returns (rows, n_skipped). Skips term-mode/unparseable proofs (see _split) and counts them."""
    rows, skipped = [], 0
    for i, (pid, full) in enumerate(source):
        if i >= n:
            break
        sp = _split(full)
        if not sp:
            skipped += 1
            continue
        header, statement, body = sp
        nl = re.findall(r"/-(.*?)-/", header, re.DOTALL)
        problem = nl[-1].strip() if nl else ""
        prompt = (
            (f"Problem: {problem}\n\n" if problem else "")
            + "Prove this Lean 4 (Mathlib) theorem. Return ONLY the tactic block that follows `:= by` "
            + "(indented; no theorem signature, no imports, no ``` fences). Do NOT use `sorry` or `admit`.\n\n"
            + statement
        )
        rows.append({
            "prompt": [{"role": "user", "content": prompt}],
            "answer": _clean(body),
            "info": json.dumps({
                "problem_id": pid,
                "header": _finite_header(header, maxhb),
                "statement": statement,
                "reference_body": body,
            }),
        })
    return rows, skipped


def _local(path):
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            yield r.get("problem_id", r.get("id", "")), r.get("full_proof", r.get("proof", ""))


def _hf(name, split):
    for r in load_dataset(name, split=split):
        yield r["problem_id"], r["full_proof"]


def build_full_code(info: dict, tactic_block: str) -> str:
    """Reconstruct a complete Lean file from the FIXED header+statement and the model's tactic block."""
    return info["header"] + info["statement"] + "\n" + tactic_block + "\n"


def _fmt_msgs(msgs) -> str:
    """Compact Lean diagnostics for repair feedback: 'L<line>: <severity>: <data>' lines, length-capped."""
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        ln = (m.get("pos") or {}).get("line", "?")
        out.append(f"L{ln} {m.get('severity', '')}: {str(m.get('data', '')).strip()}")
    return "\n".join(out)[:1500]


async def _kimina_verify(kimina_url: str, full_code: str, deadline: float) -> tuple[bool, str, str]:
    body = {"codes": [{"custom_id": "p", "proof": full_code}], "infotree_type": None}
    async with httpx.AsyncClient(timeout=deadline) as c:
        d = (await c.post(f"{kimina_url.rstrip('/')}/verify", json=body)).json()
    resp = d["results"][0].get("response", {})
    if not isinstance(resp, dict) or resp.get("error"):
        return False, "server_error", "the verifier server returned an error"
    msgs = resp.get("messages", [])
    errs = [m for m in msgs if isinstance(m, dict) and m.get("severity") == "error"]
    if errs:
        return False, "lean_error", _fmt_msgs(errs)
    # STRUCTURAL anti-sorry gate: Lean emits "declaration uses 'sorry'" (severity WARNING) for the whole sorry
    # family — plain `sorry`, `sorryAx _`, anything desugaring to sorryAx. A reward that only checked
    # severity=="error" would accept `exact sorryAx _` for ANY goal (a clean reward-hack). Reject on the
    # message itself, not the source string.
    if any(isinstance(m, dict) and "declaration uses 'sorry'" in str(m.get("data", "")).lower() for m in msgs):
        return False, "sorry_axiom", "the proof uses `sorry`/`sorryAx` (not a real proof)"
    return True, "ok", ""


async def verify_reward(info: dict, content: str, kimina_url: str, deadline: float) -> tuple[float, dict]:
    """Module-level reward core (testable without the verifiers rollout machinery): clean the tactic block,
    reject cheats (sorry/admit/empty), reconstruct onto the FIXED statement, and check with Kimina/Lean.
    Returns (reward in {0.0, 1.0}, detail dict for `state`/monitoring)."""
    proof = _clean(content)
    if not proof or _is_cheat(proof):
        return 0.0, {"verified": False, "cheated": _is_cheat(proof),
                     "feedback": "do not use `sorry`/`admit`; write a real, compiling proof"}
    try:
        ok, why, errtext = await _kimina_verify(kimina_url, build_full_code(info, proof), deadline)
    except Exception as e:
        return 0.0, {"verified": False, "error": str(e)[:100], "feedback": "the verifier timed out or errored"}
    return (1.0 if ok else 0.0), {"verified": ok, "why": why, "cheated": False, "feedback": errtext}


class LeanRepairEnv(vf.MultiTurnEnv):
    """Multi-turn variant: after a failed attempt, feed Lean's ACTUAL diagnostics back and let the model
    repair, up to `max_turns` attempts. Verification happens once per turn in env_response (the reward just
    reads the result), so a rollout costs at most `max_turns` Kimina calls — no double-verify."""

    def __init__(self, kimina_url: str, deadline: float, **kwargs):
        super().__init__(**kwargs)
        self._kimina, self._deadline = kimina_url, deadline

    @vf.stop
    async def solved(self, state, **kwargs) -> bool:
        return state.get("solved", False)

    async def env_response(self, messages, state, **kwargs):
        info = state["info"]
        if isinstance(info, str):
            info = json.loads(info)
        reward, detail = await verify_reward(info, messages[-1]["content"], self._kimina, self._deadline)
        state["solved"] = reward == 1.0
        state["attempts"] = state.get("attempts", 0) + 1
        state["lean"] = detail
        if state["solved"]:
            return [{"role": "user", "content": "Verified by Lean. ✓"}]
        fb = detail.get("feedback") or "the proof did not compile"
        return [{"role": "user", "content":
                 f"That proof did not verify. Lean reported:\n{fb}\n\n"
                 "Return a corrected tactic block (only the block after `:= by`; no `sorry`/`admit`)."}]


def load_environment(
    n: int = 100,
    kimina_url: str | None = None,
    max_heartbeats: int = 200_000,
    deadline: float = 60.0,
    dataset_name: str = "Goedel-LM/Lean-workbook-proofs",
    proofs_path: str | None = None,
    repair_turns: int = 0,
):
    """A `verifiers` environment whose reward is a real Kimina/Lean proof check.

    repair_turns=0 -> SingleTurnEnv (one shot). repair_turns>0 -> a MultiTurnEnv that feeds Lean's error back
    for up to that many repair attempts (total turns = 1 + repair_turns); reward = 1.0 if EVER verified.

    n: number of problems. kimina_url: Kimina server (else $KIMINA_URL or http://localhost:8000).
    max_heartbeats: finite Lean compute budget (0 = unlimited; the scorecard's tail lever — keep finite for RL).
    deadline: per-proof client timeout (s). proofs_path: local JSONL ({problem_id|id, full_proof|proof})
    to use instead of streaming `dataset_name` from HF.
    """
    kimina_url = (kimina_url or os.environ.get("KIMINA_URL", "http://localhost:8000")).rstrip("/")
    src = _local(proofs_path) if proofs_path else _hf(dataset_name, f"train[:{n}]")
    rows, skipped = _rows(src, n, max_heartbeats)
    if skipped:
        print(f"[lean_verifier] built {len(rows)}/{n} problems; skipped {skipped} term-mode/unparseable "
              f"(`:= by` tactic-mode only — see README limits)", file=sys.stderr)
    dataset = Dataset.from_list(rows)
    sys_prompt = "You are an expert Lean 4 theorem prover. You write correct, compiling Mathlib proofs."

    def not_cheating(completion) -> float:
        """Monitor metric (weight 0): fraction of rollouts that did NOT reach for the sorry family / admit."""
        return 0.0 if _is_cheat(_clean(completion[-1]["content"])) else 1.0

    if repair_turns > 0:
        def lean_solved(state) -> float:
            return 1.0 if state.get("solved") else 0.0

        def repair_attempts(state) -> float:
            return float(state.get("attempts", 0))

        rubric = vf.Rubric(funcs=[lean_solved, not_cheating, repair_attempts], weights=[1.0, 0.0, 0.0])
        return LeanRepairEnv(kimina_url=kimina_url, deadline=deadline, max_turns=1 + repair_turns,
                             dataset=dataset, rubric=rubric, system_prompt=sys_prompt)

    async def lean_verified(completion, info, state) -> float:
        reward, detail = await verify_reward(info, completion[-1]["content"], kimina_url, deadline)
        state["lean"] = detail
        return reward

    rubric = vf.Rubric(funcs=[lean_verified, not_cheating], weights=[1.0, 0.0])
    return vf.SingleTurnEnv(dataset=dataset, rubric=rubric, system_prompt=sys_prompt)
