# Original baseline/V3 prompts; no API credentials or initialization.
SYSTEM_PROMPT = 'You are an expert software engineer and reverse engineer. Infer the most accurate C-style function name. The function name should describe the real behavior of the function. Output ONLY one concise snake_case function name. Do not output explanations.'
USER_PROMPT = 'Generate a concise and accurate C-style function name based on the following code or semantic description.\nThe name must describe the actual behavior.\nUse snake_case.\nOutput ONLY the function name.\n\n'
SUMMARY_SYSTEM_PROMPT = 'You are an expert reverse engineer. Analyze multiple representations of the same binary function. Generate a concise semantic summary. Ignore compiler artifacts, registers and addresses. Focus on the actual behavior, input, output and operation.'
CANDIDATE_SYSTEM_PROMPT = 'You are an expert software engineer and reverse engineer. Generate possible C-style function names based on function semantics. Names must be concise and snake_case. Output only names, one per line.'
VERIFY_SYSTEM_PROMPT = 'You are an expert reverse engineer. Select the most accurate function name. The selected name must match the actual function behavior. Output ONLY one snake_case function name.'
CONFLICT_ANALYSIS_SYSTEM_PROMPT = 'You are an expert reverse engineer. Analyze whether two semantic summaries generated from different decompiler views describe the same binary function. Identify whether they are consistent, complementary, or contradictory.'
CONFLICT_RESOLUTION_SYSTEM_PROMPT = 'You are an expert reverse engineer. Resolve semantic conflicts between decompiler summaries. When summaries disagree: (1) Prefer descriptions supported by recovered control flow and data operations. (2) Prefer explicit operations over high-level speculation. (3) Preserve common semantic information between views. (4) Remove unsupported behaviors. Use pseudo code evidence to verify conflicting cases. Generate a verified semantic summary for function name recovery.'

def detect_conflict(summaries, idx):
    summary_text = '\n\n'.join([f'{field}:\n{summary}' for (field, summary) in summaries.items()])
    prompt = f'\n\nTwo semantic summaries generated from different decompiler views describe the same binary function.\n\nAnalyze their semantic relationship.\n\nSummaries:\n\n{summary_text}\n\n\nDetermine:\nImportant:\nDo not consider two summaries equivalent only because they share the same general purpose.\n\nCheck whether:\n- comparison direction is the same;\n- operators (<, >, ==) are the same;\n- processed objects are the same;\n- return conditions are the same.\n\n1. Whether the summaries are consistent.\n2. What semantic information is shared.\n3. What semantic information conflicts.\n4. Whether one summary provides more specific or reliable semantic information.\n\n\nOutput:\n\nRelationship:\n<Agreement / Complementary / Contradictory>\n\nShared Semantics:\n...\n\nConflicting Semantics:\n...\n\nPreferred Interpretation:\nOnly provide when summaries contain disagreement.\n...\n\n'
    result = call_llm(CONFLICT_ANALYSIS_SYSTEM_PROMPT, prompt, idx, 'CONFLICT_ANALYSIS')
    return result.strip()

def resolve_conflict(summaries, conflict, item, idx):
    evidence = ""
    if item is not None:
        ida_pseudo = item.get("ida_pseudo", "")
        ghidra_pseudo = item.get("ghidra_pseudo", "")
        evidence = f"\nOriginal Decompiled Evidence:\n\nIDA Pseudo Code:\n{ida_pseudo}\n\n\nGhidra Pseudo Code:\n{ghidra_pseudo}\n"
    summary_text = '\n\n'.join([f'{field}:\n{summary}' for (field, summary) in summaries.items()])
    prompt = f'\nMultiple decompiler summaries describe the same binary function.\nCandidate summaries:\n{summary_text}\nConflict analysis:\n{conflict}\n{evidence}Resolve the differences and generate one verified semantic summary.\nRequirements:\n- Describe the actual operation.\n- Include input and output.\n- Include important conditions.\n- Ignore compiler artifacts.\n- Remove unsupported behaviors.\n- Do not average contradictory descriptions.\n\nOutput only one concise English summary.\n'
    if item is None:
        prompt += "Use only the summaries and conflict analysis. Omit details that cannot be resolved from these inputs."
    result = call_llm(CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt, idx, 'CONFLICT_RESOLUTION')
    return result.strip()

def generate_consensus_summary(summaries, idx):
    summary_text = '\n\n'.join([f'{field}:\n{summary}' for (field, summary) in summaries.items()])
    prompt = f'\n\nTwo decompiler summaries describe the same binary function.\n\nGenerate a reliable semantic summary.\n\nSummaries:\n\n{summary_text}\n\n\nRequirements:\n\n1. Preserve the most precise semantic description.\n2. Prefer explicit operations over abstract descriptions.\n3. Preserve important conditions, comparisons and constraints.\n4. Do not remove mathematical operations such as:\n   - absolute difference\n   - comparison operators\n   - sorting conditions\n   - threshold relationships\n5. If two descriptions are equivalent, keep the more specific representation.\n6. Include:\n   - input object\n   - main operation\n   - important conditions\n   - return behavior\n7. If one view provides additional valid details, keep them instead of removing them.\n\nOutput only one concise English summary.\n\n'
    result = call_llm(SUMMARY_SYSTEM_PROMPT, prompt, idx, 'SUMMARY_GENERATION')
    return result.strip()
