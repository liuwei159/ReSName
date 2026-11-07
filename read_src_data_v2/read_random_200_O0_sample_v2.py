import json
import re
from pathlib import Path


# Ignore comments/whitespace without merging tokens or changing literals.
CPP_TOKEN_PATTERN = re.compile(
    r'R"(?P<delimiter>[^\s()\\]{0,16})\(.*?\)(?P=delimiter)"'
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r'|//[^\n]*|/\*.*?\*/'
    r'|[A-Za-z_]\w*'
    r'|(?:\d|\.\d)(?:[eEpP][+-]|[\w.])*'
    r'|>>=|<<=|->\*|\.\.\.|<=>|::|->|\.\*|\+\+|--|<<|>>|<=|>=|==|!='
    r'|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|##'
    r'|[^\s]',
    re.DOTALL
)


def source_key(source):
    """Return C/C++ tokens, preserving literals and operator boundaries."""
    if not isinstance(source, str):
        return ()
    return tuple(
        match.group(0)
        for match in CPP_TOKEN_PATTERN.finditer(source)
        if not match.group(0).startswith(('//', '/*'))
    )


def extract_all_o0_samples(
    input_file,
    output_file
):
    """Export all unique O0 records as a JSON array."""
    input_path = Path(input_file)
    output_path = Path(output_file)

    if input_path.resolve() == output_path.resolve():
        raise ValueError("输入文件与输出文件不能相同。")

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("输入 JSON 的顶层必须是数组。")

    if any(not isinstance(item, dict) for item in data):
        raise ValueError("每条记录必须是 JSON 对象。")

    o0_samples = [
        item for item in data
        if item.get("opt") == "O0"
    ]

    print(f"原始记录数：{len(data)}")
    print(f"O0 记录数：{len(o0_samples)}")

    # Keep the first occurrence of each normalized source and index.
    unique_samples = []
    seen_sources = set()
    seen_indices = set()
    missing_count = 0
    duplicate_source_count = 0
    duplicate_index_count = 0

    for item in o0_samples:
        key = source_key(item.get("func"))
        if not key:
            missing_count += 1
            continue
        if key in seen_sources:
            duplicate_source_count += 1
            continue
        index = item.get("index")
        if index is None:
            raise ValueError("An O0 record has no index.")
        if index in seen_indices:
            duplicate_index_count += 1
            continue
        seen_sources.add(key)
        seen_indices.add(index)
        unique_samples.append(item)

    print(f"Empty source records skipped: {missing_count}")
    print(f"Duplicate sources removed: {duplicate_source_count}")
    print(f"Duplicate indices removed: {duplicate_index_count}")
    print(f"Unique O0 candidates: {len(unique_samples)}")

    fields_to_keep = [
        "index",
        "func",
        "func_dep",
        "opt",
        "language",
        "asm",
        "ida_asm",
        "ida_pseudo",
        "ghidra_asm",
        "ghidra_pseudo"
    ]

    results = [
        {
            field: item.get(field)
            for field in fields_to_keep
        }
        for item in unique_samples
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as out_f:
        json.dump(
            results,
            out_f,
            ensure_ascii=False,
            indent=4
        )

    print(f"成功导出：{len(results)} 条 O0 记录")
    print(f"保存位置：{output_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("data_all_unique_O0.json"))
    args = parser.parse_args()
    extract_all_o0_samples(args.input, args.output)
