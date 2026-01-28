#!/usr/bin/env python3
"""
model_bench.py — broader capability benchmark for local Ollama models.

Highlights:
- Math suite: arithmetic, algebra, systems, geometry, physics, probability
- Reasoning & constraints: strict JSON schemas, extraction, multi-constraint formatting
- Coding: Python/JS/HTML/CSS/SQL prompts + optional execution harness for Python/Node
- Tool calling + agentic loop: multi-step task that must call tools and use results

Safety note: --exec-python / --exec-node will execute model-generated code locally.
Keep it off unless you're comfortable with that risk.

Examples:
  python3 model_bench.py --suite full
  python3 model_bench.py --include "qwen|llama|mistral" --suite full --json-out results.json
  python3 model_bench.py --tool --agent --tools-regex "get_time" --suite full
  python3 model_bench.py --exec-python
  python3 model_bench.py --exec-python --exec-node
  python3 model_bench.py --vision --image ./test.png
  python3 model_bench.py --vision --image ./text.png --vision-expected-text "HELLO 123"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Mapping, Union


# -----------------------------
# Ollama HTTP helpers (stdlib)
# -----------------------------

def _http_json(method: str, url: str, payload: Optional[dict] = None, timeout_s: float = 180.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {raw[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e}") from e


def ollama_tags(host: str) -> List[Dict[str, Any]]:
    return (_http_json("GET", host.rstrip("/") + "/api/tags") or {}).get("models", []) or []


def ollama_chat(
    host: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[dict]] = None,
    options: Optional[dict] = None,
    timeout_s: float = 180.0,
) -> dict:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
    if options is not None:
        payload["options"] = options
    return _http_json("POST", host.rstrip("/") + "/api/chat", payload, timeout_s=timeout_s)


# -----------------------------
# Common parsing / checks
# -----------------------------

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:\w+)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return m.group(1).strip() if m else text


def _extract_numbers(text: str) -> List[float]:
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    out: List[float] = []
    for n in nums:
        try:
            out.append(float(n))
        except Exception:
            pass
    return out


def check_exact_text(expected: str) -> Callable[[str], Tuple[bool, str]]:
    exp = expected.strip()
    def _chk(text: str) -> Tuple[bool, str]:
        got = text.strip()
        return (got == exp), f"got={got!r}"
    return _chk


def check_contains(substr: str) -> Callable[[str], Tuple[bool, str]]:
    s = substr
    def _chk(text: str) -> Tuple[bool, str]:
        ok = s in text
        return ok, f"contains={ok}"
    return _chk


def check_bullet_count(n: int) -> Callable[[str], Tuple[bool, str]]:
    def _chk(text: str) -> Tuple[bool, str]:
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        bullets = [ln for ln in lines if re.match(r"^(\-|\*|•)\s+", ln)]
        ok = len(bullets) == n
        return ok, f"bullets={len(bullets)}"
    return _chk


def check_number_close(expected: float, tol: float = 1e-6) -> Callable[[str], Tuple[bool, str]]:
    def _chk(text: str) -> Tuple[bool, str]:
        nums = _extract_numbers(text)
        if not nums:
            return False, "no_number_found"
        # accept if any number is close
        for x in nums:
            if abs(x - expected) <= tol:
                return True, f"matched={x}"
        return False, f"nums={nums[:6]} expected={expected}"
    return _chk


def check_number_close_rel(expected: float, rel_tol: float = 1e-3, abs_tol: float = 1e-6) -> Callable[[str], Tuple[bool, str]]:
    def _chk(text: str) -> Tuple[bool, str]:
        nums = _extract_numbers(text)
        if not nums:
            return False, "no_number_found"
        for x in nums:
            if abs(x - expected) <= max(abs_tol, rel_tol * max(1.0, abs(expected))):
                return True, f"matched={x}"
        return False, f"nums={nums[:6]} expected={expected}"
    return _chk


def check_fraction(expected_num: int, expected_den: int) -> Callable[[str], Tuple[bool, str]]:
    exp1 = f"{expected_num}/{expected_den}"
    exp2 = f"{expected_num} / {expected_den}"
    exp3 = str(expected_num / expected_den)
    def _chk(text: str) -> Tuple[bool, str]:
        t = text.strip()
        if exp1 in t or exp2 in t:
            return True, "fraction_ok"
        # allow decimal
        nums = _extract_numbers(t)
        if nums and any(abs(x - (expected_num/expected_den)) < 1e-6 for x in nums):
            return True, "decimal_ok"
        return False, f"got={t[:120]!r}"
    return _chk


JSONTypeSpec = Union[type, Tuple[type, ...]]

def check_json_schema(required: Mapping[str, JSONTypeSpec]) -> Callable[[str], Tuple[bool, str]]:
    def _chk(text: str) -> Tuple[bool, str]:
        t = _strip_code_fences(text)
        try:
            obj = json.loads(t)
        except Exception as e:
            return False, f"json_parse_error={e}"
        if not isinstance(obj, dict):
            return False, "json_not_object"
        for k, tp in required.items():
            if k not in obj:
                return False, f"missing_key={k}"
            if not isinstance(obj[k], tp):
                return False, f"type_mismatch_{k}"
        return True, "json_ok"
    return _chk


def check_code_has(patterns: List[str]) -> Callable[[str], Tuple[bool, str]]:
    def _chk(text: str) -> Tuple[bool, str]:
        code = _strip_code_fences(text)
        missing = [p for p in patterns if p not in code]
        return (len(missing) == 0), ("ok" if not missing else f"missing={missing}")
    return _chk


# -----------------------------
# Tool integration (optional)
# -----------------------------

def try_load_tooling() -> Tuple[Optional[List[dict]], Optional[Callable]]:
    """
    Expected repo layout:
      - tools/core.py defining TOOL_SPECS (list of OpenAI/Ollama tool specs)
      - tool_parse.py exposing run_tool_calling_loop_sync(tool_calls)

    If unavailable, returns (None, None)
    """
    try:
        from tools.core import TOOL_SPECS
        from tool_parse import run_tool_calling_loop_sync
        return TOOL_SPECS, run_tool_calling_loop_sync
    except Exception:
        return None, None


def filter_tools(tool_specs: List[dict], regex: str) -> List[dict]:
    if not regex:
        return tool_specs
    r = re.compile(regex)
    out = []
    for s in tool_specs:
        name = ((s.get("function") or {}).get("name")) or ""
        if r.search(name):
            out.append(s)
    return out


def run_agentic_tool_loop(
    host: str,
    model: str,
    tool_specs: List[dict],
    run_tool_loop_sync: Callable,
    messages: List[Dict[str, Any]],
    timeout_s: float,
    max_rounds: int = 6,
    options: Optional[dict] = None,
) -> Tuple[str, bool, List[dict]]:
    """
    Repeatedly calls /api/chat, executes any tool_calls, appends tool messages, until no more tool_calls
    or max_rounds reached.

    Returns: (final_content, used_tool, tool_result_payloads)
    tool_result_payloads: parsed JSON from tool message content when possible
    """
    used_tool = False
    tool_payloads: List[dict] = []
    for _ in range(max_rounds):
        resp = ollama_chat(host, model, messages, tools=tool_specs, options=options, timeout_s=timeout_s)
        msg = resp.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if tool_calls:
            used_tool = True
            tool_msgs = run_tool_loop_sync(tool_calls)
            for tm in tool_msgs:
                messages.append(tm)
                # tool message content is expected to be JSON string in your runtime
                c = (tm.get("content") or "").strip()
                try:
                    tool_payloads.append(json.loads(c))
                except Exception:
                    pass
            continue

        # no tools requested => finish
        return content, used_tool, tool_payloads

    # max rounds hit
    return "MAX_ROUNDS_REACHED", used_tool, tool_payloads


def _tool_time_from_payloads(payloads: List[Dict[str, Any]]) -> Optional[str]:
    # Prefer obvious keys first
    for p in payloads:
        for k in ("utc_time", "time", "now", "result"):
            v = p.get(k)
            if isinstance(v, str) and re.search(r"\d{2}:\d{2}:\d{2}", v):
                return v
    # Fallback: first string that looks like a time
    for p in payloads:
        for v in p.values():
            if isinstance(v, str) and re.search(r"\d{2}:\d{2}:\d{2}", v):
                return v
    return None


def _file_content_from_payloads(payloads: List[Dict[str, Any]]) -> Optional[str]:
    for p in payloads:
        v = p.get("content")
        if isinstance(v, str) and v.strip():
            return v
    return None


# -----------------------------
# Vision helpers (optional)
# -----------------------------

def read_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# -----------------------------
# Execution harnesses (optional)
# -----------------------------

@dataclass
class ExecSpec:
    language: str  # "python" or "node"
    filename: str
    harness_filename: str
    harness_code: str
    timeout_s: float = 6.0


def exec_python(code: str, spec: ExecSpec) -> Tuple[bool, str]:
    if shutil.which("python3") is None:
        return False, "python3_not_found"
    with tempfile.TemporaryDirectory() as td:
        user_path = os.path.join(td, spec.filename)
        harness_path = os.path.join(td, spec.harness_filename)
        with open(user_path, "w", encoding="utf-8") as f:
            f.write(code)
            f.write("\n")
        with open(harness_path, "w", encoding="utf-8") as f:
            f.write(spec.harness_code)

        try:
            # -I: isolate from user site-packages
            r = subprocess.run(
                ["python3", "-I", spec.harness_filename],
                cwd=td,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=spec.timeout_s,
                text=True,
            )
            ok = (r.returncode == 0) and ("OK" in (r.stdout + r.stderr))
            detail = (r.stdout + r.stderr).strip()[:600]
            return ok, detail if detail else f"returncode={r.returncode}"
        except subprocess.TimeoutExpired:
            return False, "timeout"
        except Exception as e:
            return False, f"exec_error={e}"


def exec_node(code: str, spec: ExecSpec) -> Tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "node_not_found"
    with tempfile.TemporaryDirectory() as td:
        user_path = os.path.join(td, spec.filename)
        harness_path = os.path.join(td, spec.harness_filename)
        with open(user_path, "w", encoding="utf-8") as f:
            f.write(code)
            f.write("\n")
        with open(harness_path, "w", encoding="utf-8") as f:
            f.write(spec.harness_code)

        try:
            r = subprocess.run(
                ["node", spec.harness_filename],
                cwd=td,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=spec.timeout_s,
                text=True,
            )
            ok = (r.returncode == 0) and ("OK" in (r.stdout + r.stderr))
            detail = (r.stdout + r.stderr).strip()[:600]
            return ok, detail if detail else f"returncode={r.returncode}"
        except subprocess.TimeoutExpired:
            return False, "timeout"
        except Exception as e:
            return False, f"exec_error={e}"


# -----------------------------
# Bench case model
# -----------------------------

@dataclass
class CaseResult:
    name: str
    status: str  # OK / FAIL / SKIP / ERR
    latency_ms: float
    details: str = ""
    used_tool: bool = False


@dataclass
class BenchCase:
    name: str
    prompt: str
    checker: Callable[[str], Tuple[bool, str]]
    category: str
    # exec support
    exec_spec: Optional[ExecSpec] = None
    require_exec: bool = False  # if True and exec not enabled => SKIP


def make_cases(suite: str) -> List[BenchCase]:
    quick = (suite == "quick")

    cases: List[BenchCase] = []

    # ---- Instruction / formatting ----
    cases += [
        BenchCase(
            name="format:3_bullets",
            category="format",
            prompt="Reply with exactly 3 bullet points about why unit tests matter. No extra text.",
            checker=check_bullet_count(3),
        ),
        BenchCase(
            name="format:strict_json_small",
            category="format",
            prompt='Return ONLY a JSON object with keys: name (string), score (number), tags (array of strings).',
            checker=check_json_schema({"name": str, "score": (int, float), "tags": list}),
        ),
        BenchCase(
            name="reason:extract",
            category="reasoning",
            prompt="Extract the email address from this text and reply with ONLY the email: 'Contact us at helpdesk+west@domain.example for support.'",
            checker=check_exact_text("helpdesk+west@domain.example"),
        ),
    ]

    # ---- Math (core) ----
    cases += [
        BenchCase("math:mul", "Compute 17*23. Reply with only the number.", check_number_close(391), "math"),
        BenchCase("math:sum_1_100", "Compute sum of integers 1..100. Reply with only the number.", check_number_close(5050), "math"),
        BenchCase("math:algebra_linear", "Solve for x: 3x - 7 = 11. Reply with only x.", check_number_close(6), "math"),
        BenchCase("math:system_exact", "Solve the system exactly: 2x + y = 7 and x - y = 1. Reply with x as a reduced fraction like a/b.", check_fraction(8, 3), "math"),
        BenchCase("math:geometry_pythag", "A right triangle has legs 5 and 12. Reply with only the hypotenuse length.", check_number_close(13), "math"),
        BenchCase("math:geometry_area_circle", "Area of a circle with radius r=3. Use pi=3.141592653589793 and reply with only the numeric area.", check_number_close_rel(28.274333882308138, rel_tol=1e-6), "math"),
        BenchCase("math:physics_fall", "An object starts from rest and falls for 3.0 s with g=9.8 m/s^2. Distance fallen = 0.5*g*t^2. Reply with only the distance in meters.", check_number_close(44.1), "math"),
        BenchCase("math:units_kmh_to_ms", "Convert 72 km/h to m/s. Reply with only the number.", check_number_close(20), "math"),
        BenchCase("math:prob_two_heads_3", "Flip a fair coin 3 times. Probability of exactly 2 heads? Reply as a fraction a/b.", check_fraction(3, 8), "math"),
    ]

    if not quick:
        cases += [
            BenchCase("math:quadratic_root", "Solve x^2 - 5x + 6 = 0. Reply with the two roots in ascending order separated by a comma, no spaces (like 1,2).", check_exact_text("2,3"), "math"),
            BenchCase("math:logic_word", "A store sells pencils at $0.25 each. You buy 14 pencils. What is the total cost? Reply with only the number in dollars (e.g., 3.50).", check_exact_text("3.50"), "math"),
            BenchCase("math:percent", "What is 15% of 260? Reply with only the number.", check_number_close(39), "math"),
        ]

    # ---- Reasoning constraints (harder) ----
    if not quick:
        cases += [
            BenchCase(
                name="reason:json_schema_hard",
                category="reasoning",
                prompt=(
                    "Return ONLY JSON with keys: "
                    "plan (array of 3 strings), "
                    "risks (array of 2 strings), "
                    "answer (string). "
                    "No extra keys."
                ),
                checker=check_json_schema({"plan": list, "risks": list, "answer": str}),
            ),
            BenchCase(
                name="reason:follow_constraints",
                category="reasoning",
                prompt=(
                    "Write a 2-sentence answer about TLS. "
                    "Sentence 1 must end with the word 'confidentiality'. "
                    "Sentence 2 must contain the substring 'certificate'. "
                    "No bullets."
                ),
                checker=lambda t: (
                    (len([x for x in re.split(r"[.!?]\s*", t.strip()) if x]) >= 2)
                    and t.strip().splitlines()[0].strip().endswith("confidentiality")
                    and ("certificate" in t),
                    "constraint_check"
                ),
            ),
        ]

    # ---- Coding prompts (static checks by default) ----
    cases += [
        BenchCase(
            name="code:python_lru",
            category="code",
            prompt=(
                "Write Python code only. Implement an LRUCache class with methods get(key) and put(key, value). "
                "Use O(1) operations (dict + doubly linked list). Include a short usage example at the bottom."
            ),
            checker=check_code_has(["class LRUCache", "def get", "def put"]),
        ),
        BenchCase(
            name="code:js_debounce",
            category="code",
            prompt=(
                "Write JavaScript code only. Implement debounce(fn, waitMs) that returns a debounced function. "
                "It must preserve 'this' and arguments. Include a small demo usage snippet."
            ),
            checker=check_code_has(["function debounce", "setTimeout", "clearTimeout"]),
        ),
        BenchCase(
            name="code:html_semantic",
            category="code",
            prompt=(
                "Write HTML only (no markdown). Create a semantic page skeleton with header/nav/main/article/aside/footer. "
                "Include a form with email input and a submit button."
            ),
            checker=check_code_has(["<header", "<nav", "<main", "<article", "<aside", "<footer", "<form", "type=\"email\""]),
        ),
        BenchCase(
            name="code:css_responsive",
            category="code",
            prompt=(
                "Write CSS only (no markdown). Create a responsive card grid: 1 column on small screens, "
                "2 columns at 600px, 3 columns at 900px. Use modern CSS (grid)."
            ),
            checker=check_code_has(["display: grid", "@media", "600", "900"]),
        ),
        BenchCase(
            name="code:sql_groupby",
            category="code",
            prompt=(
                "Write SQL only. Given table orders(id, customer_id, total, created_at), "
                "write a query to return customer_id and total_spent for orders in 2025, "
                "sorted by total_spent desc."
            ),
            checker=check_code_has(["SELECT", "GROUP BY", "ORDER BY"]),
        ),
    ]

    # ---- Deeper coding (exec harness optional) ----
    # Python exec: implement parse_duration + tests
    py_duration_harness = (
        "import user_code as u\n"
        "def ok():\n"
        "    assert u.parse_duration('1h30m') == 5400\n"
        "    assert u.parse_duration('45s') == 45\n"
        "    assert u.parse_duration('2m') == 120\n"
        "    assert u.parse_duration('3h') == 10800\n"
        "    assert u.parse_duration('1h2m3s') == 3723\n"
        "    print('OK')\n"
        "ok()\n"
    )
    cases += [
        BenchCase(
            name="code_exec:python_parse_duration",
            category="code_exec",
            prompt=(
                "Write Python code only. Implement parse_duration(s: str) -> int that parses strings like "
                "'1h30m', '45s', '2m', '1h2m3s' into total seconds. Assume valid input. "
                "No external imports."
            ),
            checker=check_code_has(["def parse_duration"]),
            exec_spec=ExecSpec(language="python", filename="user_code.py", harness_filename="harness.py", harness_code=py_duration_harness),
            require_exec=True,
        ),
    ]

    # Node exec: implement stable stringify with sorted keys
    js_stable_harness = (
        "const u = require('./user_code.js');\n"
        "function ok(){\n"
        "  const obj = {b:1,a:{d:4,c:3}};\n"
        "  const s = u.stableStringify(obj);\n"
        "  if(s !== '{\"a\":{\"c\":3,\"d\":4},\"b\":1}') throw new Error('bad:'+s);\n"
        "  console.log('OK');\n"
        "}\n"
        "ok();\n"
    )
    cases += [
        BenchCase(
            name="code_exec:js_stable_stringify",
            category="code_exec",
            prompt=(
                "Write JavaScript code only (CommonJS). Implement stableStringify(obj) that JSON-stringifies "
                "an object with keys sorted recursively. Export it via module.exports = { stableStringify }."
            ),
            checker=check_code_has(["stableStringify", "module.exports"]),
            exec_spec=ExecSpec(language="node", filename="user_code.js", harness_filename="harness.js", harness_code=js_stable_harness),
            require_exec=True,
        ),
    ]

    # In quick suite, keep exec cases but they will SKIP unless exec flags on (still useful)
    return cases


# -----------------------------
# Runner
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    ap.add_argument("--models", default="", help="Comma-separated model names. Default: all local models.")
    ap.add_argument("--include", default="", help="Regex include models.")
    ap.add_argument("--exclude", default="", help="Regex exclude models.")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--json-out", default="", help="Write results JSON to path.")
    ap.add_argument("--suite", choices=["quick", "full"], default="full")

    # tool/agent
    ap.add_argument("--tool", action="store_true", help="Run tool-calling tests (requires your repo tooling).")
    ap.add_argument("--agent", action="store_true", help="Run agentic multi-step tool loop test.")
    ap.add_argument("--tools-regex", default="", help="Regex filter for tools by function name (e.g. 'get_time|calc').")
    ap.add_argument("--tool-engine", default="registry", choices=["registry", "legacy"])

    # execution
    ap.add_argument("--exec-python", action="store_true", help="Execute Python code cases (risky).")
    ap.add_argument("--exec-node", action="store_true", help="Execute Node code cases (risky).")

    # vision
    ap.add_argument("--vision", action="store_true", help="Run vision tests (requires --image).")
    ap.add_argument("--image", default="", help="Image path for vision tests.")
    ap.add_argument("--vision-expected-text", default="", help="If set, OCR test expects this exact text (whitespace-insensitive).")

    # sampling options
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--num-predict", type=int, default=512)

    args = ap.parse_args()

    options = {"temperature": args.temperature, "num_predict": args.num_predict}

    # discover models
    try:
        tags = ollama_tags(args.host)
    except Exception as e:
        print(f"[ERR] Failed to reach Ollama at {args.host}: {e}", file=sys.stderr)
        return 2

    names: List[str] = []
    for m in tags:
        n = m.get("name")
        if isinstance(n, str) and n:
            names.append(n)
    if args.models.strip():
        wanted = {x.strip() for x in args.models.split(",") if x.strip()}
        names = [n for n in names if n in wanted]
    if args.include:
        inc = re.compile(args.include)
        names = [n for n in names if inc.search(n)]
    if args.exclude:
        exc = re.compile(args.exclude)
        names = [n for n in names if not exc.search(n)]
    if not names:
        print("[ERR] No models selected.", file=sys.stderr)
        return 1

    # load repo tooling if requested
    tool_specs = None
    run_tool_loop_sync = None
    if args.tool or args.agent:
        tool_specs, run_tool_loop_sync = try_load_tooling()
        if not tool_specs or not run_tool_loop_sync:
            print("[WARN] Tooling not importable (tools.core / tool_parse). Tool/agent tests will be skipped.", file=sys.stderr)
        else:
            os.environ["TOOL_ENGINE"] = args.tool_engine
            tool_specs = filter_tools(tool_specs, args.tools_regex)

    # vision setup
    image_b64 = ""
    if args.vision:
        if not args.image:
            print("[WARN] --vision set but no --image provided. Skipping vision tests.", file=sys.stderr)
        else:
            try:
                image_b64 = read_image_b64(args.image)
            except Exception as e:
                print(f"[WARN] Could not read image: {e}. Skipping vision tests.", file=sys.stderr)

    cases = make_cases(args.suite)

    results: Dict[str, Any] = {"host": args.host, "ran_at": time.time(), "suite": args.suite, "models": {}}

    def record(model: str, r: CaseResult) -> None:
        results["models"].setdefault(model, {"cases": []})
        results["models"][model]["cases"].append({
            "name": r.name,
            "status": r.status,
            "latency_ms": r.latency_ms,
            "details": r.details,
            "used_tool": r.used_tool,
        })

    for model in names:
        print(f"\n== {model} ==")
        model_case_results: List[CaseResult] = []

        # text cases (and exec cases if enabled)
        for c in cases:
            do_exec = False
            if c.exec_spec:
                if c.exec_spec.language == "python":
                    do_exec = args.exec_python
                elif c.exec_spec.language == "node":
                    do_exec = args.exec_node

                if c.require_exec and not do_exec:
                    r = CaseResult(c.name, "SKIP", 0.0, details="exec_disabled")
                    record(model, r)
                    model_case_results.append(r)
                    print(f"  {c.name:30} SKIP  exec_disabled")
                    continue

            messages = [{"role": "user", "content": c.prompt}]
            t0 = time.perf_counter()
            try:
                resp = ollama_chat(args.host, model, messages, tools=None, options=options, timeout_s=args.timeout)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                msg = resp.get("message") or {}
                text = (msg.get("content") or "").strip()

                ok, detail = c.checker(text)
                status = "OK" if ok else "FAIL"

                # If exec is enabled and this case has a harness, run it and override status by exec result
                if c.exec_spec and do_exec:
                    code = _strip_code_fences(text)
                    if c.exec_spec.language == "python":
                        ex_ok, ex_detail = exec_python(code, c.exec_spec)
                    else:
                        ex_ok, ex_detail = exec_node(code, c.exec_spec)
                    status = "OK" if ex_ok else "FAIL"
                    detail = f"exec:{ex_detail}"

                r = CaseResult(c.name, status, latency_ms, details=str(detail))
                record(model, r)
                model_case_results.append(r)
                print(f"  {c.name:30} {status:4}  {latency_ms:7.1f} ms  {detail}")
            except Exception as e:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                r = CaseResult(c.name, "ERR", latency_ms, details=f"{e}")
                record(model, r)
                model_case_results.append(r)
                print(f"  {c.name:30} ERR   {latency_ms:7.1f} ms  {e}")

        # tool calling test (single-step)
        if args.tool and tool_specs and run_tool_loop_sync:
            messages = [
                {"role": "system", "content": "You must use tools when needed. Do not guess."},
                {"role": "user", "content": "Call get_time with tz='UTC' and reply with ONLY the tool result."},
            ]
            t0 = time.perf_counter()
            final, used_tool, tool_payloads = run_agentic_tool_loop(
                args.host, model, tool_specs, run_tool_loop_sync, messages, args.timeout, max_rounds=3, options=options
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            tool_time = _tool_time_from_payloads(tool_payloads)
            ok = used_tool and (final != "MAX_ROUNDS_REACHED") and (tool_time is not None) and (
                final.strip() == tool_time.strip() or (tool_time in final)
            )
            status = "OK" if ok else "FAIL"
            detail = f"used_tool={used_tool} tool_time={tool_time!r} final={final[:140]!r}"
            r = CaseResult("tool:get_time", status, latency_ms, details=detail, used_tool=used_tool)
            record(model, r)
            model_case_results.append(r)
            print(f"  {'tool:get_time':30} {status:4}  {latency_ms:7.1f} ms  {detail}")

        # agentic multi-step test
        if args.agent and tool_specs and run_tool_loop_sync:
            # More sophisticated agent task requiring multiple tools and reasoning
            messages = [
                {"role": "system", "content": "You are a careful agent. Use tools instead of guessing. Read files, search the web, and get current time as needed."},
                {"role": "user", "content": (
                    "Task:\n"
                    "1) Use read_file to read the file 'AGENTS.md' in the current directory.\n"
                    "2) Use get_time with tz='UTC' to get the current time.\n"
                    "3) Then output ONLY JSON with keys: file_word_count (integer), first_topic (string), utc_time (string), steps (array of 3 strings).\n"
                    "The file_word_count should count words in AGENTS.md content.\n"
                    "The first_topic should be the first main topic mentioned in the file.\n"
                    "The utc_time must match the tool result exactly.\n"
                    "The steps array should describe what you did.\n"
                    "No extra keys."
                )},
            ]
            t0 = time.perf_counter()
            final, used_tool, tool_payloads = run_agentic_tool_loop(
                args.host, model, tool_specs, run_tool_loop_sync, messages, args.timeout, max_rounds=6, options=options
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0

            # Try to validate that final JSON includes expected fields with correct values
            ok = False
            detail = "agent_check_failed"
            tool_time = _tool_time_from_payloads(tool_payloads)
            file_content = _file_content_from_payloads(tool_payloads)
            true_word_count: Optional[int] = None
            if file_content is not None:
                true_word_count = len(re.findall(r"\S+", file_content))

            try:
                obj = json.loads(final.strip())
                if isinstance(obj, dict):
                    required_fields = ["file_word_count", "first_topic", "utc_time", "steps"]
                    if all(field in obj for field in required_fields) and isinstance(obj["steps"], list):
                        # Check utc_time matches
                        time_match = tool_time and obj["utc_time"] == tool_time
                        # Check word count matches file content (exact)
                        word_count_ok = (
                            isinstance(obj["file_word_count"], int)
                            and true_word_count is not None
                            and obj["file_word_count"] == true_word_count
                        )
                        # Check first_topic is non-empty string
                        topic_ok = isinstance(obj["first_topic"], str) and len(obj["first_topic"]) > 0

                        if time_match and word_count_ok and topic_ok and used_tool:
                            ok = True
                            detail = "agent_ok"
                        else:
                            detail = f"validation_failed time_match={time_match} word_count_ok={word_count_ok} topic_ok={topic_ok} tool_time={tool_time!r} utc_time={obj.get('utc_time')!r}"
                    else:
                        detail = f"missing_fields fields={required_fields}"
            except Exception as e:
                detail = f"final_not_json:{e}"

            status = "OK" if ok else "FAIL"
            r = CaseResult("agent:multi_tool_reasoning", status, latency_ms, details=detail, used_tool=used_tool)
            record(model, r)
            model_case_results.append(r)
            print(f"  {'agent:multi_tool_reasoning':30} {status:4}  {latency_ms:7.1f} ms  {detail}")

        # vision tests
        if args.vision and image_b64:
            # Describe
            msg = [{"role": "user", "content": "Describe the image in 1 sentence.", "images": [image_b64]}]
            t0 = time.perf_counter()
            resp = ollama_chat(args.host, model, msg, tools=None, options=options, timeout_s=args.timeout)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            txt = ((resp.get("message") or {}).get("content") or "").strip()
            ok = len(txt) > 0 and len(txt) < 500
            r = CaseResult("vision:describe", "OK" if ok else "FAIL", latency_ms, details=txt[:140])
            record(model, r)
            model_case_results.append(r)
            print(f"  {'vision:describe':30} {r.status:4}  {latency_ms:7.1f} ms  {r.details}")

            # OCR
            ocr_prompt = "If there is readable text, output it exactly. If none, output NONE. Output only the text."
            msg = [{"role": "user", "content": ocr_prompt, "images": [image_b64]}]
            t0 = time.perf_counter()
            resp = ollama_chat(args.host, model, msg, tools=None, options=options, timeout_s=args.timeout)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            txt = ((resp.get("message") or {}).get("content") or "").strip()

            if args.vision_expected_text:
                got = re.sub(r"\s+", "", txt)
                exp = re.sub(r"\s+", "", args.vision_expected_text.strip())
                ok = (got == exp)
                detail = f"got={txt!r}"
            else:
                ok = (len(txt) > 0 and len(txt) < 800)
                detail = txt[:140]

            r = CaseResult("vision:ocr", "OK" if ok else "FAIL", latency_ms, details=detail)
            record(model, r)
            model_case_results.append(r)
            print(f"  {'vision:ocr':30} {r.status:4}  {latency_ms:7.1f} ms  {detail}")

        # summary score (ignore SKIP)
        ok_count = sum(1 for r in model_case_results if r.status == "OK")
        fail_count = sum(1 for r in model_case_results if r.status in ("FAIL", "ERR"))
        scored = [r for r in model_case_results if r.status in ("OK", "FAIL", "ERR")]
        pass_rate = (ok_count / len(scored)) if scored else 0.0
        lat = [r.latency_ms for r in model_case_results if r.latency_ms > 0]
        avg_ms = statistics.mean(lat) if lat else 0.0

        results["models"][model]["pass_rate"] = pass_rate
        results["models"][model]["ok"] = ok_count
        results["models"][model]["fail_err"] = fail_count
        results["models"][model]["avg_latency_ms"] = avg_ms

        print(f"  => ok={ok_count} fail/err={fail_count} pass_rate={pass_rate*100:.1f}% avg_latency={avg_ms:.1f} ms")

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"\nWrote results to: {args.json_out}")
        except Exception as e:
            print(f"[WARN] Failed to write --json-out: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
