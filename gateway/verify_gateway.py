"""verify-gateway — a thin, dependency-free hardening proxy in front of a Kimina Lean Server.

Makes the two levers the deployability scorecard MEASURED operational, by default, for every request:
  1. finite `maxHeartbeats`  — rewrites each proof's `set_option maxHeartbeats 0` (the corpus default that
     lets ~4.5% of proofs run away) to a finite budget, so a runaway proof fails fast IN-KERNEL instead of
     eating a full timeout per rollout.
  2. per-proof deadline      — each proof gets at most GATEWAY_DEADLINE seconds; the tail can never hang the
     caller, and one slow proof can't block the others (each is forwarded independently + concurrently).

Drop-in: same `POST /verify {codes:[{custom_id, proof}], ...}` API as Kimina, so point your client at the
gateway instead of Kimina. Stdlib only (http.server + urllib) — no extra deps, trivial to run as a sidecar.

Config (env): KIMINA_UPSTREAM (real Kimina, default http://localhost:8000), GATEWAY_MAXHEARTBEATS (default
200000; 0 = leave as-is), GATEWAY_DEADLINE seconds (default 60), GATEWAY_PORT (default 8010), GATEWAY_WORKERS.
"""
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("KIMINA_UPSTREAM", "http://localhost:8000").rstrip("/")
MAXHB = int(os.environ.get("GATEWAY_MAXHEARTBEATS", "200000"))
DEADLINE = float(os.environ.get("GATEWAY_DEADLINE", "60"))
PORT = int(os.environ.get("GATEWAY_PORT", "8010"))
WORKERS = int(os.environ.get("GATEWAY_WORKERS", "8"))
_HB = re.compile(r"set_option\s+maxHeartbeats\s+\d+")


def _finite(proof: str) -> str:
    if MAXHB <= 0:
        return proof
    if _HB.search(proof):
        return _HB.sub(f"set_option maxHeartbeats {MAXHB}", proof)
    return f"set_option maxHeartbeats {MAXHB}\n" + proof


def _verify_one(code: dict) -> dict:
    """Forward one proof to Kimina with the injected budget + the per-proof deadline. On deadline/upstream
    failure, return a bounded synthetic result instead of hanging the batch."""
    proof = _finite(code.get("proof", ""))
    body = json.dumps({"codes": [{**code, "proof": proof}], "infotree_type": None}).encode()
    try:
        req = urllib.request.Request(UPSTREAM + "/verify", body, {"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=DEADLINE).read())
        return d["results"][0]
    except Exception as e:
        return {"custom_id": code.get("custom_id"),
                "response": {"error": f"gateway: {type(e).__name__}: {str(e)[:80]}"}}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/verify":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        codes = req.get("codes", [])
        with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(codes)))) as ex:
            results = list(ex.map(_verify_one, codes))
        out = json.dumps({"results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        ok = self.path.rstrip("/") in ("", "/health")
        self.send_response(200 if ok else 404)
        self.end_headers()
        if ok:
            self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"verify-gateway :{PORT} -> {UPSTREAM}  (maxHeartbeats={MAXHB}, deadline={DEADLINE}s, workers={WORKERS})",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
