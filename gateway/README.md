# verify-gateway — make the scorecard's two levers operational

A thin, **dependency-free** hardening proxy that sits in front of a Kimina Lean Server and applies, by default
and for every request, the two throughput levers the [verify-deployability scorecard](../SCORECARD-v0.2.md)
measured — turning "we measured a 2.3× lever" into "the verify endpoint is hardened."

| Lever | What the gateway does | Why (from the scorecard) |
|---|---|---|
| **finite `maxHeartbeats`** | rewrites each proof's `set_option maxHeartbeats 0` → a finite budget (default 200000) | the corpus ships `maxHeartbeats 0`; ~4.5% of proofs run away and each eats a full timeout — finite budget fails them fast **in-kernel** |
| **per-proof deadline** | each proof gets ≤ `GATEWAY_DEADLINE` s; on timeout returns a bounded synthetic result | the hung-proof tail can never hang the caller; one slow proof can't block the batch (each forwarded independently + concurrently) |

Same `POST /verify {codes:[{custom_id, proof}], ...}` API as Kimina — a drop-in: point your client at the
gateway instead of Kimina.

## Validated (against a live Kimina)
- finite-`maxHeartbeats` injection: `0 → 200000` (unit) and valid proofs still verify through the gateway
  (errors=0); a 3-proof batch returns concurrently.
- per-proof deadline: a ~25 s proof sent with `GATEWAY_DEADLINE=2` returns in **2.0 s** with
  `{"error": "gateway: TimeoutError: timed out"}` — bounded, never hangs.

## Run
```bash
KIMINA_UPSTREAM=http://<kimina>:8000 GATEWAY_DEADLINE=60 GATEWAY_MAXHEARTBEATS=200000 \
  python verify_gateway.py        # listens on :8010 (GATEWAY_PORT)
# then point the throughput harness / RL rollout loop at the gateway:
#   python ../verify_throughput.py --base http://<gateway>:8010 ...
```
On k3s: [`../deploy/verify-gateway.yaml`](../deploy/verify-gateway.yaml) (script as a ConfigMap on a
`python:3.12-slim` pod in front of the `kimina` service; no image build).

Config (env): `KIMINA_UPSTREAM`, `GATEWAY_MAXHEARTBEATS` (0 = leave as-is), `GATEWAY_DEADLINE` (s),
`GATEWAY_PORT`, `GATEWAY_WORKERS`.

## Limit
Stdlib `ThreadingHTTPServer` — fine as a sidecar/single-node hardening layer at rollout-loop concurrency, not
a high-QPS load balancer. The RSS-threshold worker recycle (Kimina's `MAX_REPL_USES`, for the Lean leak
lean4 #5321/#6753) is configured on the Kimina server itself, not here.
