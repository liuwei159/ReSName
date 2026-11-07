import re
from typing import Any, Dict, List

def build_eval_prompt(code_snippet: str, candidates: List[Dict[str, Any]]) -> str:
    """
    构造输入给大模型的评分 prompt。
    关键点：
    1. 大模型只能看到 Function Name 0、Function Name 1 等编号。
    2. 不暴露 asm、ida_asm、ida_pseudo、ghidra_asm、ghidra_pseudo 等来源。
    3. 候选函数名数量自动适配，不固定写死为 11。
    """
    candidate_count = len(candidates)
    last_id = candidate_count - 1
    candidate_text = '\n'.join((f"{candidate['function_label']}: {candidate['function_name']}" for candidate in candidates))
    prompt = f"\nYou are an experienced software developer. I will provide you with a function code snippet and {candidate_count} candidate function names. Do not skip any of them. Make sure to return exactly {candidate_count} evaluations.\nYour task is to evaluate each function name based on the code snippet and the following two criteria (C):\nA function name should not be penalized for omitting minor implementation details.\nEvaluate whether the name correctly conveys the main semantic purpose rather than every internal operation.\n**C1. Accuracy Score (1–5):**\nRate how well the function name reflects the functionality of the provided code snippet. Use a scale from 1 to 5, where a higher score indicates more accuracy. 1 means completely irrelevant or misleading, and 5 means perfectly aligned and clearly expresses the function's functionality.\nNote that an accurate function name should adhere to the following coding conventions:\n(1) Clearly convey the primary purpose of the function and accurately reflect its core functionality;\n(2) Be concise yet descriptive, avoiding unnecessary words or redundancy;\n(3) Avoid using generic or ambiguous terms and ensure clarity and readability.\n**C2. Naming Format Check (1 or 0):**\nCheck whether the function name follows common naming conventions for the target programming language. Since the given code is C/C++, snake_case and lowerCamelCase are acceptable naming styles. Assign 1 for compliance and 0 for non-compliance.\nOnce your evaluation is complete, present the {candidate_count} results and explanations for each function name in the following format:\n**Function Name 0: <function name>**\nC1: <1 - 5>. <Detailed Explanations>\nC2: <1 or 0>. <Detailed Explanations>\n......\n**Function Name {last_id}: <function name>**\nC1: <1 - 5>. <Detailed Explanations>\nC2: <1 or 0>. <Detailed Explanations>\nDo not add any extra sections before or after the evaluations.\nThe following is a code snippet and {candidate_count} candidate function names under evaluation:\nThe code snippet:\n```cpp\n{code_snippet}\n```\nThe {candidate_count} function names under evaluation:\n{candidate_text}\n".strip()
    return prompt

def parse_llm_response(response_text):
    pattern = re.compile('\\*{0,2}Function Name\\s+(\\d+)\\s*:\\s*(.*?)\\s*\\*{0,2}\\s*\\nC1\\s*:\\s*([1-5])\\s*[\\.\\。]\\s*(.*?)\\nC2\\s*:\\s*([01])\\s*[\\.\\。]\\s*(.*?)(?=\\n\\s*\\*{0,2}Function Name|\\Z)', re.S)
    results = []
    for m in pattern.finditer(response_text):
        results.append({'function_id': int(m.group(1)), 'function_name_in_response': m.group(2).strip(), 'c1_score': int(m.group(3)), 'c1_explanation': m.group(4).strip(), 'c2_score': int(m.group(5)), 'c2_explanation': m.group(6).strip()})
    return results
