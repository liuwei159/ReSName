#!/usr/bin/env python3
"""Compile JSON/JSONL source records at O0 and extract func0 bytes on Linux.

C sources use gcc and C++ sources use g++, based on dataset language labels.
The bytes come from a relocatable ELF object, not a linked executable.
Existing assembly/decompiler fields are preserved, not regenerated.
Only Python's standard library and GNU gcc, g++, nm, objdump are required.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


def load_records(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        records = json.loads(text)
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
        raise ValueError("Input must contain an array of objects or JSONL records.")
    indices = [json.dumps(x.get("index"), sort_keys=True) for x in records]
    if any(x.get("index") is None for x in records):
        raise ValueError("Every record must have an index.")
    if len(set(indices)) != len(indices):
        raise ValueError("Duplicate indices found; deduplicate the input first.")
    return records


def save_json(path, value):
    """Replace output atomically, keeping the previous checkpoint on failure."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=4)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def attach_metadata(records, metadata_path=None):
    """Restore language/opt by index, after checking the original source."""
    metadata = {}
    if metadata_path:
        metadata = {x["index"]: x for x in load_records(metadata_path)}
    enriched = []
    for original in records:
        item = dict(original)
        reference = metadata.get(item["index"])
        if metadata_path and reference is None:
            raise ValueError(f"Index {item['index']} is absent from metadata.")
        if reference is not None:
            if any(item.get(k) != reference.get(k) for k in ("func", "func_dep")):
                raise ValueError(f"Source differs from metadata at index {item['index']}.")
            for field in ("language", "opt"):
                if field in item and item[field] != reference.get(field):
                    raise ValueError(f"Conflicting {field} at index {item['index']}.")
                item[field] = reference.get(field)
        if item.get("language") not in ("c", "cpp"):
            raise ValueError(
                f"Missing/unsupported language at index {item['index']}; "
                "provide --metadata humaneval-decompile.json."
            )
        if item.get("opt") != "O0":
            raise ValueError(f"Expected O0 metadata at index {item['index']}.")
        enriched.append(item)
    return enriched


def run_command(command, timeout):
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=environment, check=False
    )
    if result.returncode:
        raise RuntimeError(
            "Command failed: " + repr(command) + "\n" +
            (result.stderr or result.stdout).strip()
        )
    return result


def select_symbol(nm_text, target="func0"):
    symbols = []
    for line in nm_text.splitlines():
        parts = line.split()
        # GNU nm --format=posix: name type address size
        if len(parts) < 4 or parts[1] not in ("T", "t"):
            continue
        name = parts[0]
        # Exact C symbol or Itanium C++ encoding of this function name.
        if name == target or name.startswith("_Z" + str(len(target)) + target):
            symbols.append((name, int(parts[2], 16), int(parts[3], 16)))
    if len(symbols) != 1:
        raise ValueError(
            f"Expected exactly one symbol for {target!r}; found {len(symbols)}. "
            "Refusing to select another function."
        )
    if symbols[0][2] <= 0:
        raise ValueError("Target function has an empty or unknown symbol size.")
    return symbols[0]


def parse_function_bytes(disassembly, symbol, start, size):
    header = re.compile(r"^\s*[0-9a-fA-F]+ <([^>]+)>:\s*$")
    instruction = re.compile(r"^\s*([0-9a-fA-F]+):\s+(.+)$")
    inside = False
    result = bytearray()
    for line in disassembly.splitlines():
        match = header.match(line)
        if match:
            if inside:
                break
            inside = match.group(1) == symbol
            continue
        match = instruction.match(line) if inside else None
        if not match:
            continue
        address = int(match.group(1), 16)
        if not start <= address < start + size:
            continue
        octets = []
        for word in match.group(2).split():
            if not re.fullmatch(r"[0-9a-fA-F]{2}", word):
                break
            octets.append(int(word, 16))
        if not octets:
            continue
        if address != start + len(result):
            raise ValueError("Disassembly byte addresses are not contiguous.")
        remaining = size - len(result)
        if len(octets) > remaining:
            raise ValueError("Disassembly extends beyond the target symbol.")
        result.extend(octets)
    if len(result) != size:
        raise ValueError(f"Symbol has {size} bytes, but extracted {len(result)}.")
    return bytes(result)


def generate(args):
    input_path = args.input.resolve()
    output_path = (args.output or input_path.with_name(
        input_path.stem + "_with_binary.json"
    )).resolve()
    if input_path == output_path:
        raise ValueError("Input and output must be different files.")
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists: {output_path}; use --overwrite explicitly.")
    records = attach_metadata(load_records(input_path), args.metadata)
    if not records:
        raise ValueError("Input contains no records.")
    tools = {}
    for name, executable in (("c", args.c_compiler), ("cpp", args.compiler), ("nm", "nm"), ("objdump", "objdump")):
        tools[name] = shutil.which(executable)
        if not tools[name]:
            raise RuntimeError(f"Missing tool: {executable}. Use a Linux GNU build environment.")
    machines = {}
    for language in ("c", "cpp"):
        machine = run_command([tools[language], "-dumpmachine"], args.timeout).stdout.strip()
        if "linux" not in machine or not any(x in machine for x in ("x86_64", "amd64")):
            raise RuntimeError(f"Expected an x86-64 Linux compiler; got {machine!r}.")
        machines[language] = machine
    versions = {
        name: run_command([executable, "--version"], args.timeout).stdout.splitlines()[0]
        for name, executable in tools.items()
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root = (args.work_dir or output_path.parent / "binary_artifacts").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    # A fresh directory prevents stale object files from a previous failed run.
    run_dir = Path(tempfile.mkdtemp(prefix="o0_run_", dir=artifact_root))
    flags = ["-c", "-O0", "-m64", "-fno-lto"]
    provenance = {
        "input_file": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "compiler_targets": machines, "tool_versions": versions,
        "compile_flags": flags, "language_counts": dict(Counter(x["language"] for x in records)),
        "host": platform.platform(), "artifact_directory": str(run_dir),
        "binary_kind": "unlinked_ELF_object_function_bytes",
        "note": "Original asm/IDA/Ghidra fields are retained, not regenerated."
    }
    if args.metadata:
        provenance["metadata_file"] = str(args.metadata.resolve())
        provenance["metadata_sha256"] = hashlib.sha256(args.metadata.read_bytes()).hexdigest()
    output_fields = (
        "index", "func", "func_dep", "opt", "language", "binary_code",
        "asm", "ida_asm", "ida_pseudo", "ghidra_asm", "ghidra_pseudo",
    )
    sample_reports = []
    results = []
    for position, original in enumerate(records, 1):
        item = dict(original)
        # Explicitly clear stale binary fields if the input already contains them.
        for key in ("binary", "binary_code", "binary_size", "binary_symbol", "compile_error", "binary_error", "binary_file", "binary_build"):
            item.pop(key, None)
        item.update(binary_code=None, compile_status="not_started", binary_status="pending")
        stem = f"sample_{position:04d}"
        language = item["language"]
        source_path = run_dir / (stem + (".c" if language == "c" else ".cpp"))
        object_path = run_dir / (stem + ".o")
        try:
            source = item.get("func")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Missing function source.")
            dependencies = item.get("func_dep") or ""
            source_path.write_text(dependencies + "\n\n" + source + "\n", encoding="utf-8")
            command = [tools[language], *flags, str(source_path), "-o", str(object_path)]
            item["binary_build"] = {"command": command, "source_file": str(source_path), "language": language}
            compilation = run_command(command, args.timeout)
            (run_dir / (stem + ".compile.log")).write_text(compilation.stderr, encoding="utf-8")
            item["compile_status"] = "success"
            item["binary"] = str(object_path)
            with object_path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise ValueError("Compiler did not produce an ELF object.")
            symbols = run_command([tools["nm"], "-S", "--defined-only", "--format=posix", str(object_path)], args.timeout)
            symbol, start, size = select_symbol(symbols.stdout, args.symbol)
            disassembly = run_command([
                tools["objdump"], "-d", "--insn-width=16", "--disassemble=" + symbol,
                str(object_path)
            ], args.timeout).stdout
            (run_dir / (stem + ".disassembly.txt")).write_text(disassembly, encoding="utf-8")
            code = parse_function_bytes(disassembly, symbol, start, size)
            binary_path = run_dir / (stem + ".bin")
            binary_path.write_bytes(code)
            item.update(binary_code=code.hex(), binary_size=len(code),
                        binary_symbol=symbol, binary_file=str(binary_path), binary_status="success")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            item["binary_status"] = "failed"
            item["binary_error"] = str(error)
            if item["compile_status"] != "success":
                item["compile_status"] = "failed"
                item["compile_error"] = str(error)
            (run_dir / (stem + ".error.log")).write_text(str(error), encoding="utf-8")
        results.append({field: item.get(field) for field in output_fields})
        sample_reports.append({
            "index": item["index"],
            **{key: item[key] for key in (
                "compile_status", "binary_status", "binary_build", "binary",
                "binary_size", "binary_symbol", "binary_file", "compile_error", "binary_error",
            ) if key in item},
        })
        save_json(output_path, results)
        print(f"[{position}/{len(records)}] index={item['index']} "
              f"status={item['binary_status']} bytes={item.get('binary_size', 0)}", flush=True)
    provenance["counts"] = dict(Counter(x["binary_status"] for x in sample_reports))
    provenance["total"] = len(results)
    provenance["samples"] = sample_reports
    report_path = output_path.with_name(output_path.stem + "_report.json")
    save_json(report_path, provenance)
    print(f"Output: {output_path}\nReport: {report_path}\nCounts: {provenance['counts']}")
    return 1 if provenance["counts"].get("failed") else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Input JSON array or JSONL.")
    parser.add_argument("--output", type=Path, help="Output JSON array.")
    parser.add_argument("--work-dir", type=Path, help="Parent directory for compilation artifacts.")
    parser.add_argument("--metadata", type=Path, help="Original dataset, to restore language and O0 labels by index.")
    parser.add_argument("--c-compiler", default="gcc", help="Linux x86-64 GNU C compiler.")
    parser.add_argument("--compiler", default="g++", help="Linux x86-64 GNU C++ compiler.")
    parser.add_argument("--symbol", default="func0", help="Target function name before C++ mangling.")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per external command, in seconds.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output.")
    args = parser.parse_args()
    try:
        return generate(args)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
