"""Shared, resumable baseline/control/ablation runner. No requests at import time."""
import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import naming_prompts as prompts

VIEWS = {
    "binary": ("binary_code", "Binary", "binary_code_name", "binary_summary_name"),
    "raw_asm": ("asm", "Raw Assembly", "raw_asm_code_name", "raw_asm_summary_name"),
    "ida_asm": ("ida_asm", "IDA Assembly", "ida_asm_code_name", "ida_asm_summary_name"),
    "ghidra_asm": ("ghidra_asm", "Ghidra Assembly", "ghidra_asm_code_name", "ghidra_asm_summary_name"),
    "ida_pseudo": ("ida_pseudo", "IDA Pseudo", "ida_pseudo_code_name", "ida_pseudo_summary_name"),
    "ghidra_pseudo": ("ghidra_pseudo", "Ghidra Pseudo", "ghidra_pseudo_code_name", "ghidra_pseudo_summary_name"),
}
BASELINES = [view + "_" + kind for view in VIEWS for kind in ("direct", "summary")]
MULTIVIEW = ["v3", "v3_single", "v3_single_no_evidence", "v3_no_evidence", "simple_fusion_5",
             "simple_fusion_single", "concat_5", "ida_summary_5", "ghidra_summary_5"]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(data, list) or any(not isinstance(x, dict) or x.get("index") is None for x in data):
        raise ValueError("Input must be JSON/JSONL objects with index fields.")
    if len({digest(x['index']) for x in data}) != len(data):
        raise ValueError("Duplicate indices are not allowed.")
    return data


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=4)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if temp and temp.exists():
            temp.unlink()


def summary(item, view):
    canonical = "llm_" + view + "_summary"
    old = "llm-" + ("asm" if view == "raw_asm" else view.replace("_", "-")) + "-summary"
    values = [item[k] for k in (canonical, old) if isinstance(item.get(k), str) and item[k].strip()]
    if len(set(values)) > 1:
        raise ValueError(f"Conflicting summary aliases: {canonical}, {old}.")
    return values[0] if values else ""


def sanitize(content, code=False):
    text = str(content or "").strip()
    if code:
        text = re.sub(r"_Z\d+func\d+[^\s:(]*", "XXX", text)
        text = re.sub(r"\bfunc\d+\b", "XXX", text)
        # Strip address labels, not comparison expressions or C++ templates.
        text = re.sub(r"(?m)^(\s*[0-9a-fA-F]+\s+)<[^\n>]+>:", r"\1XXX:", text)
        text = re.sub(r"(\b(?:call\w*|j\w*)\s+[^\n<]*?)<[^\n>]+>", r"\1", text)
    return text


def clean_name(text):
    value = str(text or "").strip()
    value = re.sub(r"^```(?:[A-Za-z]+)?\s*|\s*```$", "", value).strip()
    value = re.sub(r"^(?:[-*]|\d+[.)])\s*", "", value)
    value = re.sub(r"^(?:function\s+name|name)\s*:\s*", "", value, flags=re.I)
    value = value.strip('`\"\' ')
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", value):
        raise ValueError("Expected exactly one snake_case name.")
    if value in {"xxx", "func0", "unknown", "function", "name", "empty", "no_data"}:
        raise ValueError("Non-semantic function name.")
    return value


def parse_relationship(text):
    # Parse only the declared label, never keywords in the explanation.
    match = re.search(r"(?im)^\s*\*{0,2}Relationship\*{0,2}\s*:\s*\*{0,2}\s*(Agreement|Complementary|Contradictory)\b", text)
    if not match:
        raise ValueError("Missing valid Relationship label.")
    return match.group(1).capitalize()


class Runner:
    def __init__(self, args, cache_path):
        self.args = args
        self.cache_path = cache_path
        self.cache = {}
        self.client = None
        self.trace = []
        if cache_path.exists():
            lines = cache_path.read_text(encoding="utf-8").splitlines()
            for position, line in enumerate(lines):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    if position == len(lines)-1:
                        # Repair an interrupted final append before adding new entries.
                        cache_path.write_text("\n".join(lines[:position]) + "\n", encoding="utf-8")
                        break
                    raise
                if record.get("status") == "success":
                    self.cache[record["key"]] = record["output"]

    def call(self, index, stage, system, user, tokens=300, validator=None, repair_instruction=None):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = digest([index, stage, self.args.model, self.args.base_url, messages, tokens, 0, os.environ.get("LLM_EXTRA_BODY", "{}")])
        self.trace.append(key)
        if key in self.cache:
            output = self.cache[key]
            if validator:
                validator(output)
            return output
        if self.client is None:
            if not self.args.model or not self.args.base_url:
                raise RuntimeError("Configure LLM_MODEL and LLM_BASE_URL or supply CLI options.")
            from openai import OpenAI
            api_key = os.environ.get("LLM_API_KEY")
            if not api_key:
                raise RuntimeError("Set LLM_API_KEY before running real requests.")
            self.client = OpenAI(api_key=api_key, base_url=self.args.base_url, timeout=120, max_retries=0)
        for attempt in range(self.args.retries):
            started = time.monotonic()
            record = {"key": key, "index": index, "stage": stage, "model": self.args.model,
                      "messages": messages, "max_tokens": tokens, "temperature": 0,
                      "extra_body": os.environ.get("LLM_EXTRA_BODY", "{}"), "attempt": attempt+1,
                      "request_key": digest([index, stage, self.args.model, self.args.base_url, messages, tokens])}
            try:
                response = self.client.chat.completions.create(
                    model=self.args.model, messages=messages, temperature=0,
                    max_tokens=tokens, stream=False, extra_body=json.loads(os.environ.get("LLM_EXTRA_BODY", "{}")))
                choice = response.choices[0]
                output = (choice.message.content or "").strip()
                record.update(output=output, finish_reason=choice.finish_reason,
                              usage=response.usage.model_dump() if response.usage else None)
                if not output or choice.finish_reason != "stop":
                    raise ValueError("Empty, truncated or incomplete model response.")
                if validator:
                    validator(output)
                record["status"] = "success"
            except Exception as error:
                record.update(status="failed", error=str(error))
            record["elapsed_seconds"] = time.monotonic()-started
            with self.cache_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+"\n")
                stream.flush()
                os.fsync(stream.fileno())
            if record["status"] == "success":
                self.cache[key] = output
                return output
            if repair_instruction and record.get("output") is not None:
                # Correct formatting only; do not invent or extract a name from prose.
                # Build a new list so the previous log retains its actual request.
                messages = [
                    {"role":"system", "content":system},
                    {"role":"user", "content":user + "\n\n" + repair_instruction}
                ]
            if attempt+1 < self.args.retries:
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(record["error"])

    def legacy_call(self, system, user, index, stage):
        return self.call(index, stage, system, user, self.args.summary_tokens,
                         parse_relationship if stage == "CONFLICT_ANALYSIS" else None)

    def name(self, content, label, index, code=False):
        user = prompts.USER_PROMPT + "Input type: " + label + "\n\n" + sanitize(content, code)
        return clean_name(self.call(index, "SINGLE_NAME", prompts.SYSTEM_PROMPT, user,
                                    self.args.name_tokens, clean_name,
                                    "Your previous response did not meet the required output format. "
                                    "Return exactly ONE concise snake_case function name. "
                                    "Do not include analysis, explanations, sentences, or Markdown. "
                                    "Use only the supplied input to determine the function purpose."))

    def candidate_names(self, content, index):
        def parse(text):
            values = []
            for line in text.splitlines():
                if line.strip():
                    name = clean_name(line)
                    if name not in values:
                        values.append(name)
            if not 1 <= len(values) <= 5:
                raise ValueError("Expected 1 to 5 distinct valid candidates.")
            return values
        user = ("Generate 5 possible C-style function names based on this semantic summary.\n\n"
                "Summary:\n" + content + "\n\nOutput only snake_case names, one per line.")
        raw = self.call(index, "CANDIDATE_GENERATION", prompts.CANDIDATE_SYSTEM_PROMPT,
                        user, self.args.summary_tokens, parse)
        return parse(raw)

    def select(self, content, candidates, index):
        user = ("Function semantic summary:\n\n" + content + "\n\nCandidate function names:\n" +
                "\n".join(f"{i+1}. {n}" for i, n in enumerate(candidates)) +
                "\n\nSelect the best function name.")
        def validate(text):
            if clean_name(text) not in candidates:
                raise ValueError("Selected name is not in the candidate set.")
        return clean_name(self.call(index, "NAME_VERIFICATION", prompts.VERIFY_SYSTEM_PROMPT,
                                    user, self.args.name_tokens, validate))

    def process(self, item, mode, baseline):
        index = item["index"]
        if baseline:
            view, kind = mode.rsplit("_", 1)
            field, label, direct_key, summary_key = VIEWS[view]
            content = item[field] if kind == "direct" else summary(item, view)
            name = self.name(content, label + (" Code" if kind == "direct" else " Summary"),
                             index, kind == "direct")
            return {"status": "success", "final_name": name,
                    "output_field": direct_key if kind == "direct" else summary_key}
        summaries = {"llm_"+v+"_summary": summary(item, v)
                     for v in ("ida_pseudo", "ghidra_pseudo")}
        relation = None
        analysis = None
        route = mode
        if mode == "ida_summary_5":
            content = summary(item, "ida_pseudo")
        elif mode == "ghidra_summary_5":
            content = summary(item, "ghidra_pseudo")
        elif mode == "concat_5":
            content = "\n\n".join(k+":\n"+v for k,v in summaries.items())
        elif mode.startswith("simple_fusion"):
            content = prompts.generate_consensus_summary(summaries, index)
        else:
            analysis = prompts.detect_conflict(summaries, index)
            relation = parse_relationship(analysis)
            if relation == "Contradictory" and mode != "v3_no_evidence":
                evidence = None if mode == "v3_single_no_evidence" else {
                    k: sanitize(item[k], code=True) for k in ("ida_pseudo", "ghidra_pseudo")
                }
                content = prompts.resolve_conflict(summaries, analysis,
                                                   evidence, index)
                route = "conflict_resolution"
            else:
                content = prompts.generate_consensus_summary(summaries, index)
                route = "consensus_generation"
        single = mode in ("v3_single", "v3_single_no_evidence", "simple_fusion_single")
        candidates = [] if single else self.candidate_names(content, index)
        name = self.name(content, "Function Semantic Summary", index) if single else self.select(content, candidates, index)
        return {"status": "success", "final_name": name, "summary": content,
                "candidate_names": candidates, "relationship": relation,
                "conflict_analysis": analysis, "route": route}


def preflight(rows, modes, baseline):
    for item in rows:
        for mode in modes:
            if baseline:
                view, kind = mode.rsplit("_", 1)
                required = [(view, item.get(VIEWS[view][0]) if kind == "direct" else summary(item, view))]
            else:
                views = ["ida_pseudo"] if mode == "ida_summary_5" else ["ghidra_pseudo"] if mode == "ghidra_summary_5" else ["ida_pseudo", "ghidra_pseudo"]
                required = [(v+" summary", summary(item,v)) for v in views]
                if mode in ("v3", "v3_single"):
                    required += [(v, item.get(v)) for v in views]
            for label, value in required:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"index={item['index']}, mode={mode}: missing {label}.")


def main(baseline=False):
    parser = argparse.ArgumentParser(description="Baseline naming" if baseline else "V3 controls and ablations")
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parent.parent / "results_317/summary/summary.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--modes", default="all" if baseline else "v3_single")
    parser.add_argument("--limit", type=int, default=0, help="0 means all records.")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL"))
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--summary-tokens", type=int, default=1000)
    parser.add_argument("--name-tokens", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-code-update", action="store_true",
                        help="Explicitly accept a code-only update, preserving prior successes and recording migration.")
    args = parser.parse_args()
    allowed = BASELINES if baseline else MULTIVIEW
    modes = allowed if args.modes == "all" else list(dict.fromkeys(args.modes.split(",")))
    if args.limit < 0 or args.retries < 1 or args.summary_tokens < 1 or args.name_tokens < 1:
        parser.error("Invalid numeric parameters.")
    if not modes or any(mode not in allowed for mode in modes):
        parser.error("Valid modes: " + ",".join(allowed))
    rows = load(args.input)
    if args.limit:
        rows = rows[:args.limit]
    preflight(rows, modes, baseline)
    output = (args.output or Path(__file__).resolve().parent.parent / ("results_317/naming/baselines.json" if baseline else "results_317/naming/multiview.json")).resolve()
    if output == args.input.resolve():
        parser.error("Output must not overwrite input.")
    print(f"Samples={len(rows)}; modes={','.join(modes)}; output={output}")
    if args.dry_run:
        print("Preflight passed. No API requests or output writes.")
        return 0
    if not os.environ.get("LLM_API_KEY"):
        raise RuntimeError("Set LLM_API_KEY before running real requests.")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.with_suffix(".manifest.json")
    config = {k:v for k,v in vars(args).items() if k not in ("modes", "output", "dry_run", "resume_code_update")}
    config["input"] = str(args.input.resolve())
    code_hash = digest([(Path(__file__).read_text(encoding="utf-8")), Path(prompts.__file__).read_text(encoding="utf-8")])
    signature = digest([config, rows, code_hash, baseline])
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous["signature"] != signature:
            # Recreate the prior signature with its old code hash. This proves
            # that data, model, settings, limit and runner kind have not changed.
            compatible = previous.get("code_hash") and previous["signature"] == digest([config, rows, previous["code_hash"], baseline])
            if not args.resume_code_update or not compatible:
                raise ValueError("Input/config/code differs. For a code-only update use --resume-code-update; otherwise use a new output path.")
            previous.setdefault("code_update_history",[]).append({
                "old_signature":previous["signature"], "old_code_hash":previous["code_hash"],
                "new_code_hash":code_hash, "accepted_at":time.strftime('%Y-%m-%d %H:%M:%S'),
                "note":"Explicit code-only migration. Prior successes retained; failed/pending modes use updated code."})
            previous.update(signature=signature, code_hash=code_hash)
            save(manifest_path, previous)
    elif output.exists():
        raise ValueError("Existing output lacks a manifest. Use a new output path.")
    else:
        save(manifest_path, {"signature":signature, "config":{k:str(v) if isinstance(v,Path) else v for k,v in config.items()}, "code_hash":code_hash})
    existing = {digest(x["index"]):x for x in load(output)} if output.exists() else {}
    results = [existing.get(digest(row["index"]), dict(row, naming_results={})) for row in rows]
    runner = Runner(args, output.with_suffix(".calls.jsonl"))
    prompts.call_llm = runner.legacy_call
    failed = 0
    for position, (item, result) in enumerate(zip(rows, results), 1):
        for mode in modes:
            if result["naming_results"].get(mode,{}).get("status") == "success":
                continue
            try:
                runner.trace = []
                value = runner.process(item, mode, baseline)
                if baseline:
                    result[value["output_field"]] = value["final_name"]
            except Exception as error:
                value = {"status":"failed", "error":str(error), "final_name":None}
                failed += 1
            value["call_keys"] = list(runner.trace)
            result["naming_results"][mode] = value
            save(output, results)
            print(f"[{position}/{len(rows)}] index={item['index']} {mode}: {value['status']}", flush=True)
    totals = {"api_attempts":0, "failed_attempts":0, "elapsed_seconds":0,
              "prompt_tokens":0, "completion_tokens":0, "usage_missing_attempts":0}
    if runner.cache_path.exists():
        for line in runner.cache_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            totals["api_attempts"] += 1
            totals["failed_attempts"] += record["status"] != "success"
            totals["elapsed_seconds"] += record["elapsed_seconds"]
            if record.get("usage"):
                for key in ("prompt_tokens", "completion_tokens"):
                    totals[key] += record["usage"].get(key) or 0
            else:
                totals["usage_missing_attempts"] += 1
    save(output.with_suffix(".report.json"), {
        "totals_across_all_runs_and_modes": totals,
        "note": "Stages are shared by content cache; do not attribute shared run totals to each mode.",
        "modes": {mode: {
            "success":sum(x["naming_results"].get(mode,{}).get("status")=="success" for x in results),
            "failed":sum(x["naming_results"].get(mode,{}).get("status")=="failed" for x in results),
            "logical_calls_for_successes":sum(len(x["naming_results"].get(mode,{}).get("call_keys",[]))
                for x in results if x["naming_results"].get(mode,{}).get("status")=="success")
        } for mode in modes}})
    print(f"Finished; failures this run={failed}; successful stages are cached for retry.")
    return 1 if failed else 0
