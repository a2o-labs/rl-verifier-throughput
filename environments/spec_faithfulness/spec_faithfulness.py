"""spec-faithfulness — a mechanically-checked specification-faithfulness environment for `verifiers`.

The model writes a formal **specification** (Verus `requires`/`ensures` clauses) for a function from its
natural-language description and signature. Reward is NOT an LLM judge and NOT a string match — it is a
**differential check against a real Verus verifier**:

  a spec is faithful  ⇔  it VERIFIES the reference (correct) implementation  AND  REJECTS every known-buggy one.

This is the mechanical core of Verus-SpecGym (arXiv 2605.26457): an unfaithful spec fails in one of two ways —
too strong (rejects the correct impl) or too weak (accepts a buggy impl). Both are caught here without an
expert gold spec or an LLM judge (the paper notes LLM judges miss ~26% of failures).

Anti-gaming: the model supplies ONLY the spec clauses; the env fixes the function signature and ALL the
implementations, so the policy cannot prove an easier function. Vacuous specs (`requires false`,
`ensures true`) are self-defeating — they let the buggy impls verify too, so they reject nothing and score low.

Needs a `verus` binary (https://github.com/verus-lang/verus). Set VERUS_CMD (default `verus`) or pass
`verus_cmd=[...]`; the runner shells out per candidate file. GPU-free, on-prem.
"""
import json
import os
import re
import subprocess
import tempfile

import verifiers as vf
from datasets import Dataset

_RESULT = re.compile(r"(\d+)\s+verified,\s+(\d+)\s+error")
_FENCE = re.compile(r"```(?:rust|verus)?\s*(.*?)```", re.DOTALL)
# clauses that would let a spec cheat by short-circuiting verification rather than constraining behaviour
_SPEC_CHEAT = re.compile(r"\b(assume|admit|external_body|no_verify)\b")

# Seed problems. Each: a fixed signature + a correct body + buggy mutants. The model writes the spec.
# Chosen to be clean in Verus (bool / comparison / min-max — no overflow), so a buggy mutant fails for the
# SPEC reason, not an incidental arithmetic error. (A real run would stream Verus-SpecBench's 581 tasks.)
PROBLEMS = [
    {"name": "max", "nl": "Return the maximum of two unsigned 64-bit integers `a` and `b`.",
     "sig": "fn max(a: u64, b: u64) -> (res: u64)",
     "correct": "if a >= b { a } else { b }", "buggy": ["a", "b", "0"]},
    {"name": "min", "nl": "Return the minimum of two unsigned 64-bit integers `a` and `b`.",
     "sig": "fn min(a: u64, b: u64) -> (res: u64)",
     "correct": "if a <= b { a } else { b }", "buggy": ["a", "b", "0"]},
    {"name": "max3", "nl": "Return the maximum of three unsigned 64-bit integers `a`, `b`, `c`.",
     "sig": "fn max3(a: u64, b: u64, c: u64) -> (res: u64)",
     "correct": "if a >= b { if a >= c { a } else { c } } else { if b >= c { b } else { c } }",
     "buggy": ["a", "b", "c", "if a >= b { a } else { b }"]},
    {"name": "min3", "nl": "Return the minimum of three unsigned 64-bit integers `a`, `b`, `c`.",
     "sig": "fn min3(a: u64, b: u64, c: u64) -> (res: u64)",
     "correct": "if a <= b { if a <= c { a } else { c } } else { if b <= c { b } else { c } }",
     "buggy": ["a", "b", "c", "if a <= b { a } else { b }"]},
    {"name": "clamp", "nl": "Clamp `x` into the inclusive range [`lo`, `hi`] (you may assume `lo <= hi`): "
     "return `lo` if `x < lo`, `hi` if `x > hi`, otherwise `x`.",
     "sig": "fn clamp(x: u64, lo: u64, hi: u64) -> (res: u64)",
     "correct": "if x < lo { lo } else if x > hi { hi } else { x }",
     "buggy": ["x", "lo", "hi", "if x < lo { lo } else { x }"]},
    {"name": "is_even", "nl": "Return true iff the unsigned integer `n` is even.",
     "sig": "fn is_even(n: u64) -> (res: bool)",
     "correct": "n % 2 == 0", "buggy": ["true", "false", "n % 2 == 1"]},
    {"name": "is_odd", "nl": "Return true iff the unsigned integer `n` is odd.",
     "sig": "fn is_odd(n: u64) -> (res: bool)",
     "correct": "n % 2 == 1", "buggy": ["true", "false", "n % 2 == 0"]},
    {"name": "leq", "nl": "Return true iff `a` is less than or equal to `b`.",
     "sig": "fn leq(a: u64, b: u64) -> (res: bool)",
     "correct": "a <= b", "buggy": ["a < b", "a >= b", "true"]},
    {"name": "geq", "nl": "Return true iff `a` is greater than or equal to `b`.",
     "sig": "fn geq(a: u64, b: u64) -> (res: bool)",
     "correct": "a >= b", "buggy": ["a > b", "a <= b", "false"]},
    {"name": "equal", "nl": "Return true iff `a` equals `b`.",
     "sig": "fn equal(a: u64, b: u64) -> (res: bool)",
     "correct": "a == b", "buggy": ["a <= b", "a != b", "true", "false"]},
    {"name": "not_equal", "nl": "Return true iff `a` is different from `b`.",
     "sig": "fn not_equal(a: u64, b: u64) -> (res: bool)",
     "correct": "a != b", "buggy": ["a < b", "a == b", "true"]},
    {"name": "in_range", "nl": "Return true iff `x` lies in the inclusive range [`lo`, `hi`].",
     "sig": "fn in_range(x: u64, lo: u64, hi: u64) -> (res: bool)",
     "correct": "lo <= x && x <= hi", "buggy": ["lo <= x", "x <= hi", "lo < x && x < hi", "true"]},
    {"name": "logical_and", "nl": "Return the logical AND of booleans `a` and `b`.",
     "sig": "fn logical_and(a: bool, b: bool) -> (res: bool)",
     "correct": "a && b", "buggy": ["a || b", "a", "b", "true"]},
    {"name": "logical_or", "nl": "Return the logical OR of booleans `a` and `b`.",
     "sig": "fn logical_or(a: bool, b: bool) -> (res: bool)",
     "correct": "a || b", "buggy": ["a && b", "a", "b", "false"]},
    {"name": "logical_xor", "nl": "Return the exclusive-or (XOR) of booleans `a` and `b`.",
     "sig": "fn logical_xor(a: bool, b: bool) -> (res: bool)",
     "correct": "a ^ b", "buggy": ["a && b", "a || b", "a == b"]},
    {"name": "logical_implies", "nl": "Return the logical implication `a ==> b` for booleans `a` and `b`.",
     "sig": "fn logical_implies(a: bool, b: bool) -> (res: bool)",
     "correct": "!a || b", "buggy": ["a || b", "a && b", "b", "!a"]},
]


def _clean(content: str) -> str:
    m = _FENCE.search(content)
    if m:
        content = m.group(1)
    return content.strip()


def compose(problem: dict, spec: str, body: str) -> str:
    """Assemble a full Verus file: fixed signature + the model's spec clauses + a given body."""
    return (
        "use vstd::prelude::*;\nverus! {\n"
        f"{problem['sig']}\n{spec}\n{{\n    {body}\n}}\n"
        "}\nfn main() {}\n"
    )


def run_verus(rs_text: str, verus_cmd, timeout: float = 60.0) -> tuple[bool, str]:
    """Return (verified, raw_output). verified = Verus reports 0 errors and exits 0."""
    with tempfile.NamedTemporaryFile("w", suffix=".rs", delete=False) as f:
        f.write(rs_text)
        path = f.name
    try:
        p = subprocess.run([*verus_cmd, path], capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        m = _RESULT.search(out)
        verified = (p.returncode == 0) and bool(m) and int(m.group(2)) == 0
        return verified, out[-1500:]
    except subprocess.TimeoutExpired:
        return False, "verus timeout"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def faithfulness_reward(problem: dict, spec_text: str, verus_cmd, timeout: float = 60.0) -> tuple[float, dict]:
    """Differential reward: correct impl must verify AND every buggy impl must be rejected.
    Returns (reward in [0,1], detail). Reward = 0 if the spec rejects the correct impl (too strong / broken)
    or smuggles a verification-bypass clause; otherwise the fraction of buggy impls correctly rejected."""
    spec = _clean(spec_text)
    if not spec or _SPEC_CHEAT.search(spec):
        return 0.0, {"reward": 0.0, "why": "empty_or_cheat", "cheated": bool(_SPEC_CHEAT.search(spec))}
    correct_ok, _ = run_verus(compose(problem, spec, problem["correct"]), verus_cmd, timeout)
    if not correct_ok:
        return 0.0, {"reward": 0.0, "why": "rejects_correct_impl", "correct_verifies": False}
    rejected = 0
    for body in problem["buggy"]:
        ok, _ = run_verus(compose(problem, spec, body), verus_cmd, timeout)
        if not ok:
            rejected += 1
    n = len(problem["buggy"])
    reward = rejected / n if n else 1.0
    return reward, {"reward": reward, "why": "faithful" if reward == 1.0 else "too_weak",
                    "correct_verifies": True, "buggy_rejected": rejected, "buggy_total": n}


def _dataset() -> Dataset:
    rows = []
    for p in PROBLEMS:
        prompt = (
            f"Write a Verus specification for this function.\n\nProblem: {p['nl']}\n\n"
            f"Signature:\n{p['sig']}\n\n"
            "Output ONLY the `requires`/`ensures` clauses that go between the signature and the body "
            "(e.g. `ensures res >= a, res >= b,`). The implementation is fixed and hidden; your spec must "
            "accept the correct implementation and rule out incorrect ones. No `assume`/`admit`."
        )
        rows.append({"prompt": [{"role": "user", "content": prompt}],
                     "answer": "", "info": json.dumps({"name": p["name"]})})
    return Dataset.from_list(rows)


def load_environment(verus_cmd=None, timeout: float = 60.0) -> vf.SingleTurnEnv:
    """SingleTurnEnv: the model writes a Verus spec; reward = differential faithfulness vs a real Verus run.
    verus_cmd: command list to invoke Verus (default $VERUS_CMD split, else ['verus']). timeout: per-run (s)."""
    if verus_cmd is None:
        verus_cmd = os.environ.get("VERUS_CMD", "verus").split()
    by_name = {p["name"]: p for p in PROBLEMS}

    async def spec_faithful(completion, info, state) -> float:
        problem = by_name[info["name"]]
        reward, detail = faithfulness_reward(problem, completion[-1]["content"], verus_cmd, timeout)
        state["verus"] = detail
        return reward

    def not_cheating(completion) -> float:
        return 0.0 if _SPEC_CHEAT.search(_clean(completion[-1]["content"])) else 1.0

    rubric = vf.Rubric(funcs=[spec_faithful, not_cheating], weights=[1.0, 0.0])
    return vf.SingleTurnEnv(dataset=_dataset(), rubric=rubric,
                            system_prompt="You are an expert in formal verification with Verus (Rust). "
                                          "You write precise, faithful specifications.")
