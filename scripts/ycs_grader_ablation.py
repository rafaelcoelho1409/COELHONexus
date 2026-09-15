"""YCS grader ablation harness — grader ON vs BYPASSED answer comparison.

Usage (from repo root,two arms, no code changes between them):
  1. Deploy once (this script + the YCS_ABLATE_NO_GRADE gate in grade/node.py).
  2. Arm A (grader ON, default):  python3 scripts/ycs_grader_ablation.py --arm on
  3. Flip flag (fast restart, no rebuild):
       kubectl set env -n coelhonexus-dev deploy/coelhonexus-fastapi YCS_ABLATE_NO_GRADE=1
  4. Arm B (bypassed):            python3 scripts/ycs_grader_ablation.py --arm off
  5. Clear flag: kubectl set env -n coelhonexus-dev deploy/coelhonexus-fastapi YCS_ABLATE_NO_GRADE-
  6. Compare /tmp/ycs_ablation_{on,off}.json blind (answers shuffled by --judge).

Each arm asks the same 10 fixed questions on fresh threads (no cache),
force_mode=standard, and records wall time + answer + citations +
grounded. Provider flakiness affects both arms equally-ish; wall times
are indicative, answer quality is the verdict.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

QUESTIONS = [
    "O que o video Como estudar melhor diz sobre o PISA?",
    "O que o episodio com Jose Siqueira diz sobre saude mental e emocoes?",
    "O que os videos dizem sobre motivacao e curiosidade nos estudos?",
    "Como a ansiedade aparece nos videos do canal?",
    "O que e dito sobre interpretacao de graficos e escolaridade?",
    "Que relacao os videos estabelecem entre sono e desempenho nos estudos?",
    "O que os videos dizem sobre TDAH em adultos?",
    "Como os videos tratam a busca por avaliacao psicologica?",
    "O que e dito sobre comparacao entre levantamento brasileiro e PISA?",
    "Que conselhos praticos os videos dao para estudar melhor?",
]

POD_CMD = [
    "kubectl", "exec", "-n", "coelhonexus-dev",
    "deploy/coelhonexus-fastapi", "-c", "coelhonexus-fastapi", "--",
]

PY_ASK = """
import json, urllib.request, time
body = json.dumps({'question': %r, 'thread_id': %r, 'force_mode': 'standard'}).encode()
req = urllib.request.Request('http://localhost:8000/api/v1/ycs/agents/search', data=body, headers={'Content-Type': 'application/json'}, method='POST')
t = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=590) as r:
        d = json.loads(r.read())
    print(json.dumps({'wall_s': round(time.monotonic() - t, 1),
                      'answer': d.get('answer', ''), 'mode': d.get('mode'),
                      'grounded': d.get('grounded'),
                      'citations': d.get('citations', []),
                      'error': None}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({'wall_s': round(time.monotonic() - t, 1), 'answer': '',
                      'mode': None, 'grounded': False, 'citations': [],
                      'error': f'{type(e).__name__}: {str(e)[:200]}'}))
"""


def ask(question: str, thread_id: str) -> dict:
    code = PY_ASK % (question, thread_id)
    out = subprocess.run(
        POD_CMD + ["timeout", "600", "python3", "-c", code],
        capture_output=True, text=True, timeout=660,
    )
    try:
        return json.loads((out.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"wall_s": -1, "answer": "", "error": f"harness: {(out.stderr or '')[:200]}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["on", "off"])
    args = ap.parse_args()
    results = []
    for i, q in enumerate(QUESTIONS):
        tid = f"ablation-{args.arm}-{i:02d}-{int(time.time()) % 100000}"
        print(f"[{args.arm}] Q{i + 1}/10: {q[:60]}...", flush=True)
        r = ask(q, tid)
        r["question"] = q
        results.append(r)
        print(f"   wall={r.get('wall_s')}s grounded={r.get('grounded')} "
              f"cits={len(r.get('citations') or [])} err={r.get('error')}", flush=True)
    path = f"/tmp/ycs_ablation_{args.arm}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if not r.get("error") and r.get("answer"))
    walls = [r["wall_s"] for r in results if r.get("wall_s", -1) > 0]
    print(f"done: {ok}/10 answered, median wall={sorted(walls)[len(walls)//2] if walls else '-'}s -> {path}")


if __name__ == "__main__":
    sys.exit(main())
