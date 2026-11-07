# -*- coding: utf-8 -*-

import json
import os
import time
import re
from pathlib import Path

from openai import OpenAI


# ============================================================
# 配置区域
# ============================================================

API_KEY = os.environ.get("LLM_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "未检测到环境变量 LLM_API_KEY，请先设置 API Key。"
    )


BASE_URL = os.environ["LLM_BASE_URL"]

MODEL_NAME = os.environ["LLM_MODEL"]


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)


# ============================================================
# 文件路径配置
# ============================================================

# 输入：
# JSONL 格式
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = PROJECT_ROOT / "results_317" / "summary"
INPUT_FILE = str(
    PROJECT_ROOT / "src_data_add_binary_code_v2" / "data_all_unique_O0_with_binary.json"
)
OUTPUT_FILE = str(RESULT_DIR / "summary.json")
LOG_FILE = str(RESULT_DIR / "summary.log")


# ============================================================
# 处理样本数量
# ============================================================

# 测试时先设置为 1
SAMPLE_LIMIT = None

# 如果以后要处理全部样本，改成：
# SAMPLE_LIMIT = None


# ============================================================
# 模型参数
# ============================================================

TEMPERATURE = 0.0

MAX_TOKENS = 256

SLEEP_SECONDS = 1.0


# ============================================================
# Log 记录
# ============================================================

def write_log(content):
    """
    将内容追加写入日志文件。
    """

    log_dir = os.path.dirname(LOG_FILE)

    if log_dir:
        os.makedirs(
            log_dir,
            exist_ok=True
        )

    with open(
        LOG_FILE,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(content)

        if not content.endswith("\n"):
            f.write("\n")

        # 立即刷新到磁盘
        f.flush()

        os.fsync(
            f.fileno()
        )


# ============================================================
# 清理 Binary Code
# ============================================================

def clean_binary_content(binary_content):
    """
    清理机器码。

    当前 binary_code 一般为连续十六进制字符串，例如：

    f30f1efa554889e5...

    这里只删除首尾空白，不改变机器码本身。
    """

    if not binary_content:
        return ""

    return binary_content.strip()


# ============================================================
# 清理 Assembly
# ============================================================

def clean_asm_content(asm_content):
    """
    清理汇编代码中的部分符号信息，
    尽量降低函数名等信息对摘要生成的影响。
    """

    if not asm_content:
        return ""

    cleaned = asm_content


    # --------------------------------------------------------
    # 删除 <...> 中的符号信息
    #
    # 例如：
    #
    # callq 1234 <malloc@plt>
    #
    # 变为：
    #
    # callq 1234
    # --------------------------------------------------------

    cleaned = re.sub(
        r"<[^>]+>",
        "",
        cleaned
    )


    # --------------------------------------------------------
    # 替换普通函数名 func0
    # --------------------------------------------------------

    cleaned = re.sub(
        r"\bfunc0\b",
        "XXX",
        cleaned
    )


    # --------------------------------------------------------
    # 替换可能存在的 C++ mangled func0
    #
    # 例如：
    #
    # _Z5func0...
    # --------------------------------------------------------

    cleaned = re.sub(
        r"_Z\d+func0[^\s:]*",
        "XXX",
        cleaned
    )


    return cleaned.strip()


# ============================================================
# 清理 Pseudo Code
# ============================================================

def clean_pseudo_content(pseudo_content):
    """
    清理伪代码中的函数名。
    """

    if not pseudo_content:
        return ""

    cleaned = pseudo_content


    # 普通 func0
    cleaned = re.sub(
        r"\bfunc0\b",
        "XXX",
        cleaned
    )


    # C++ mangled func0
    cleaned = re.sub(
        r"_Z\d+func0[^\s:(]*",
        "XXX",
        cleaned
    )


    return cleaned.strip()


# ============================================================
# LLM 通用摘要生成
# ============================================================

def generate_summary(
    content,
    prompt_prefix,
    max_tokens=MAX_TOKENS,
    sample_index=None,
    stage=None
):
    """
    通用摘要生成函数。

    日志记录：
    - index
    - stage
    - model
    - system prompt
    - user prompt
    - finish_reason
    - model response
    - error
    """

    if not content:
        return None


    # --------------------------------------------------------
    # 构建 User Prompt
    # --------------------------------------------------------

    prompt = (
        f"{prompt_prefix}\n\n"
        f"```\n"
        f"{content}\n"
        f"```"
    )


    # --------------------------------------------------------
    # System Prompt
    # --------------------------------------------------------

    system_prompt = (
        "You are an expert reverse engineer. "
        "Analyze the provided code and summarize its high-level logic."
    )


    # --------------------------------------------------------
    # 实际发送给 API 的 messages
    # --------------------------------------------------------

    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": prompt
        }
    ]


    try:

        response = client.chat.completions.create(

            model=MODEL_NAME,

            messages=messages,

            stream=False,

            max_tokens=max_tokens,

            # 所有视角统一
            temperature=TEMPERATURE,

            extra_body={
                **json.loads(os.environ.get("LLM_EXTRA_BODY", "{}"))
            }
        )


        choice = response.choices[0]

        message = choice.message


        print(
            f"    finish_reason: "
            f"{choice.finish_reason}"
        )


        # ====================================================
        # message.content 为 None
        # ====================================================

        if message.content is None:

            print(
                "    Warning: "
                "message.content is None"
            )


            write_log(
                f"\n"
                f"{'=' * 100}\n"
                f"INDEX: {sample_index}\n"
                f"STAGE: {stage}\n"
                f"MODEL: {MODEL_NAME}\n"
                f"TEMPERATURE: {TEMPERATURE}\n"
                f"\n"
                f"SYSTEM PROMPT:\n"
                f"{system_prompt}\n"
                f"\n"
                f"USER PROMPT:\n"
                f"{prompt}\n"
                f"\n"
                f"FINISH REASON:\n"
                f"{choice.finish_reason}\n"
                f"\n"
                f"MODEL RESPONSE:\n"
                f"None\n"
                f"\n"
                f"ERROR:\n"
                f"message.content is None\n"
                f"{'=' * 100}\n"
            )


            return None


        # ----------------------------------------------------
        # 获取摘要
        # ----------------------------------------------------

        summary = message.content.strip()


        # ====================================================
        # 返回空字符串
        # ====================================================

        if not summary:

            print(
                "    Warning: "
                "empty summary returned"
            )


            write_log(
                f"\n"
                f"{'=' * 100}\n"
                f"INDEX: {sample_index}\n"
                f"STAGE: {stage}\n"
                f"MODEL: {MODEL_NAME}\n"
                f"TEMPERATURE: {TEMPERATURE}\n"
                f"\n"
                f"SYSTEM PROMPT:\n"
                f"{system_prompt}\n"
                f"\n"
                f"USER PROMPT:\n"
                f"{prompt}\n"
                f"\n"
                f"FINISH REASON:\n"
                f"{choice.finish_reason}\n"
                f"\n"
                f"MODEL RESPONSE:\n"
                f"{message.content}\n"
                f"\n"
                f"ERROR:\n"
                f"Empty summary returned\n"
                f"{'=' * 100}\n"
            )


            return None


        # ====================================================
        # 正常情况写日志
        # ====================================================

        write_log(
            f"\n"
            f"{'=' * 100}\n"
            f"INDEX: {sample_index}\n"
            f"STAGE: {stage}\n"
            f"MODEL: {MODEL_NAME}\n"
            f"TEMPERATURE: {TEMPERATURE}\n"
            f"\n"
            f"SYSTEM PROMPT:\n"
            f"{system_prompt}\n"
            f"\n"
            f"USER PROMPT:\n"
            f"{prompt}\n"
            f"\n"
            f"FINISH REASON:\n"
            f"{choice.finish_reason}\n"
            f"\n"
            f"MODEL RESPONSE:\n"
            f"{summary}\n"
            f"{'=' * 100}\n"
        )


        return summary


    except Exception as e:

        print(
            f"    Error generating summary: "
            f"{e}"
        )


        # ====================================================
        # API 异常记录日志
        # ====================================================

        write_log(
            f"\n"
            f"{'=' * 100}\n"
            f"INDEX: {sample_index}\n"
            f"STAGE: {stage}\n"
            f"MODEL: {MODEL_NAME}\n"
            f"TEMPERATURE: {TEMPERATURE}\n"
            f"\n"
            f"SYSTEM PROMPT:\n"
            f"{system_prompt}\n"
            f"\n"
            f"USER PROMPT:\n"
            f"{prompt}\n"
            f"\n"
            f"ERROR:\n"
            f"{str(e)}\n"
            f"{'=' * 100}\n"
        )


        return None


# ============================================================
# Binary Code Summary
# ============================================================

def process_binary_code(
    binary_content,
    sample_index=None,
    stage=None
):
    """
    处理机器码并生成一句话摘要。
    """

    if not binary_content:
        return None


    processed_binary = clean_binary_content(
        binary_content
    )


    if not processed_binary:
        return None


    prompt = (
        "Please generate a short comment in one sentence "
        "for the following function:"
    )


    return generate_summary(
        processed_binary,
        prompt,
        sample_index=sample_index,
        stage=stage
    )


# ============================================================
# Assembly Summary
# ============================================================

def process_asm_code(
    asm_content,
    sample_index=None,
    stage=None
):
    """
    处理汇编代码并生成一句话摘要。
    """

    if not asm_content:
        return None


    processed_asm = clean_asm_content(
        asm_content
    )


    if not processed_asm:
        return None


    # Binary / Raw ASM / IDA ASM / Ghidra ASM
    # 使用相同摘要 Prompt

    prompt = (
        "Please generate a short comment in one sentence "
        "for the following function:"
    )


    return generate_summary(
        processed_asm,
        prompt,
        sample_index=sample_index,
        stage=stage
    )


# ============================================================
# Pseudo Code Summary
# ============================================================

def process_pseudo_code(
    pseudo_content,
    sample_index=None,
    stage=None
):
    """
    处理伪代码并生成一句话摘要。
    """

    if not pseudo_content:
        return None


    processed_pseudo = clean_pseudo_content(
        pseudo_content
    )


    if not processed_pseudo:
        return None


    # 为保证不同视角 Prompt 尽量一致，
    # 这里也使用相同的一句话摘要要求

    prompt = (
        "Please generate a short comment in one sentence "
        "for the following function:"
    )


    return generate_summary(
        processed_pseudo,
        prompt,
        sample_index=sample_index,
        stage=stage
    )


# ============================================================
# 处理单条数据
# ============================================================

def process_one_item(
    data,
    index,
    total
):
    """
    每个样本生成 6 个摘要：

    1. Binary Code
    2. Raw ASM
    3. IDA ASM
    4. Ghidra ASM
    5. IDA Pseudo
    6. Ghidra Pseudo
    """

    sample_index = data.get(
        "index",
        "N/A"
    )


    print(
        f"Processing item "
        f"{index + 1}/{total} "
        f"(Index: {sample_index})..."
    )


    # ========================================================
    # 1. Binary Code Summary
    # ========================================================

    print(
        "  [1/6] Binary Code Summary..."
    )


    binary_summary = process_binary_code(

        data.get(
            "binary_code"
        ),

        sample_index=sample_index,

        stage="BINARY_CODE_SUMMARY"
    )


    data[
        "llm_binary_summary"
    ] = binary_summary


    print(
        "  Finished: "
        "llm_binary_summary"
    )


    # ========================================================
    # 2. Raw ASM Summary
    # ========================================================

    print(
        "  [2/6] Raw ASM Summary..."
    )


    raw_asm_summary = process_asm_code(

        data.get(
            "asm"
        ),

        sample_index=sample_index,

        stage="RAW_ASM_SUMMARY"
    )


    data[
        "llm_raw_asm_summary"
    ] = raw_asm_summary


    print(
        "  Finished: "
        "llm_raw_asm_summary"
    )


    # ========================================================
    # 3. IDA ASM Summary
    # ========================================================

    print(
        "  [3/6] IDA ASM Summary..."
    )


    ida_asm_summary = process_asm_code(

        data.get(
            "ida_asm"
        ),

        sample_index=sample_index,

        stage="IDA_ASM_SUMMARY"
    )


    data[
        "llm_ida_asm_summary"
    ] = ida_asm_summary


    print(
        "  Finished: "
        "llm_ida_asm_summary"
    )


    # ========================================================
    # 4. Ghidra ASM Summary
    # ========================================================

    print(
        "  [4/6] Ghidra ASM Summary..."
    )


    ghidra_asm_summary = process_asm_code(

        data.get(
            "ghidra_asm"
        ),

        sample_index=sample_index,

        stage="GHIDRA_ASM_SUMMARY"
    )


    data[
        "llm_ghidra_asm_summary"
    ] = ghidra_asm_summary


    print(
        "  Finished: "
        "llm_ghidra_asm_summary"
    )


    # ========================================================
    # 5. IDA Pseudo Summary
    # ========================================================

    print(
        "  [5/6] IDA Pseudo Summary..."
    )


    ida_pseudo_summary = process_pseudo_code(

        data.get(
            "ida_pseudo"
        ),

        sample_index=sample_index,

        stage="IDA_PSEUDO_SUMMARY"
    )


    data[
        "llm_ida_pseudo_summary"
    ] = ida_pseudo_summary


    print(
        "  Finished: "
        "llm_ida_pseudo_summary"
    )


    # ========================================================
    # 6. Ghidra Pseudo Summary
    # ========================================================

    print(
        "  [6/6] Ghidra Pseudo Summary..."
    )


    ghidra_pseudo_summary = process_pseudo_code(

        data.get(
            "ghidra_pseudo"
        ),

        sample_index=sample_index,

        stage="GHIDRA_PSEUDO_SUMMARY"
    )


    data[
        "llm_ghidra_pseudo_summary"
    ] = ghidra_pseudo_summary


    print(
        "  Finished: "
        "llm_ghidra_pseudo_summary"
    )


    return data


# ============================================================
# 读取 JSON / JSONL
# ============================================================

def load_data(input_file):
    """
    输入既支持 JSONL，也支持标准 JSON。

    无论输入是什么格式，
    最终 OUTPUT_FILE 都统一输出标准 JSON。
    """

    is_jsonl = input_file.lower().endswith(
        ".jsonl"
    )


    with open(
        input_file,
        "r",
        encoding="utf-8"
    ) as f:


        if is_jsonl:

            data_list = [
                json.loads(line)

                for line in f

                if line.strip()
            ]


        else:

            data_list = json.load(
                f
            )


    return data_list


# ============================================================
# 保存标准 JSON
# ============================================================

def save_json(
    results
):
    """
    将当前所有结果保存成标准 JSON 数组。

    每完成一个样本都会调用一次，
    避免程序中断导致之前的结果丢失。
    """

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f_out:


        json.dump(

            results,

            f_out,

            ensure_ascii=False,

            indent=4
        )


        f_out.flush()


        os.fsync(
            f_out.fileno()
        )


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # 检查输入文件
    # --------------------------------------------------------

    if not os.path.exists(
        INPUT_FILE
    ):

        print(
            f"Error: Input file "
            f"{INPUT_FILE} not found."
        )

        return


    print(
        f"Starting processing "
        f"{INPUT_FILE}..."
    )


    # --------------------------------------------------------
    # 读取数据
    # --------------------------------------------------------

    data_list = load_data(
        INPUT_FILE
    )


    # ========================================================
    # SAMPLE_LIMIT
    # ========================================================

    if SAMPLE_LIMIT is not None:

        data_list = data_list[
            :SAMPLE_LIMIT
        ]


    total = len(
        data_list
    )


    print(
        f"Processing "
        f"{total} samples."
    )


    print(
        f"Output JSON: "
        f"{OUTPUT_FILE}"
    )


    print(
        f"Log: "
        f"{LOG_FILE}"
    )


    print(
        f"Temperature: "
        f"{TEMPERATURE}"
    )


    # --------------------------------------------------------
    # 创建输出目录
    # --------------------------------------------------------

    output_dir = os.path.dirname(
        OUTPUT_FILE
    )


    if output_dir:

        os.makedirs(
            output_dir,
            exist_ok=True
        )


    # --------------------------------------------------------
    # 创建日志目录
    # --------------------------------------------------------

    log_dir = os.path.dirname(
        LOG_FILE
    )


    if log_dir:

        os.makedirs(
            log_dir,
            exist_ok=True
        )


    # ========================================================
    # 清空旧日志
    # ========================================================

    if os.path.exists(
        LOG_FILE
    ):

        os.remove(
            LOG_FILE
        )


    # ========================================================
    # 开始运行日志
    # ========================================================

    write_log(
        f"\n"
        f"{'#' * 100}\n"
        f"NEW RUN\n"
        f"INPUT_FILE: {INPUT_FILE}\n"
        f"OUTPUT_FILE: {OUTPUT_FILE}\n"
        f"MODEL: {MODEL_NAME}\n"
        f"TEMPERATURE: {TEMPERATURE}\n"
        f"SAMPLE_LIMIT: {SAMPLE_LIMIT}\n"
        f"{'#' * 100}\n"
    )


    # ========================================================
    # 统一输出为标准 JSON
    # ========================================================

    results = []


    for index, data in enumerate(
        data_list
    ):

        processed_data = process_one_item(

            data,

            index,

            total
        )


        results.append(
            processed_data
        )


        # ----------------------------------------------------
        # 每完成一个样本，
        # 就更新一次完整 JSON 文件
        # ----------------------------------------------------

        save_json(
            results
        )


        print(
            f"Saved item "
            f"{index + 1}/{total} "
            f"to JSON file."
        )


        print(
            "-" * 80
        )


        # ----------------------------------------------------
        # 每个样本处理完成后暂停
        # ----------------------------------------------------

        time.sleep(
            SLEEP_SECONDS
        )


    # ========================================================
    # 完成日志
    # ========================================================

    write_log(
        f"\n"
        f"{'#' * 100}\n"
        f"PROCESSING COMPLETE\n"
        f"TOTAL: {total}\n"
        f"{'#' * 100}\n"
    )


    print()

    print(
        "=" * 80
    )

    print(
        "Processing complete!"
    )

    print(
        "=" * 80
    )


    print(
        f"Total samples: "
        f"{total}"
    )


    print(
        f"Results saved to: "
        f"{OUTPUT_FILE}"
    )


    print(
        f"Log saved to: "
        f"{LOG_FILE}"
    )


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    main()