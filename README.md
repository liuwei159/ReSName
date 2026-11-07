# Conflict-Aware Dual-View Function Naming

The proposed configuration is v3_single. It independently summarizes IDA and Ghidra pseudocode, classifies summary relations, resolves contradictions with pseudocode evidence, and directly generates one snake_case name.

## Setup
Python 3.9+ and the packages in requirements.txt are required. Binary extraction additionally needs Linux GNU gcc, g++, nm and objdump.

Configure LLM_API_KEY, LLM_MODEL and LLM_BASE_URL locally. The client expects a chat-completions-compatible server. LLM_EXTRA_BODY is optional JSON for server extensions. Model names, service URLs and credentials are not bundled.

## Structure
- generate-summary: six-view summary generator.
- generate-funcname: twelve baselines, proposed method, controls and ablations.
- llm-eval: anonymous C1/C2 evaluation.
- read_src_data_v2: all-unique O0 extraction.
- src_data_add_binary_code_v2: binary extraction and experiment input.
- results: final names, per-sample scores and aggregate scores.

Read [EXPERIMENTS.md](EXPERIMENTS.md) for commands. New intermediate outputs use ignored results_317 directories.

## Experimental scope
317 unique-source O0 records include 153 C and 164 C++ functions. Source token deduplication ignores comments and whitespace, preserving literals and operators. This does not establish task-level independence.

Published scores are historical measurements. Model changes do not guarantee the same scores. Summary field prefixes have been generalized to llm_. Configuration selection used overlapping development/evaluation data.

The no-evidence ablation retains the original conflict-resolution system prompt mentioning evidence, but receives no pseudocode and adds a user instruction restricting inputs to summaries and analysis.

## Data provenance
The local dataset version is related to HumanEval-Decompile. The original paper describes C data; provenance of local C++ and IDA enrichment still requires attribution. Original decompiler views were retained; newly extracted object bytes did not regenerate them.

Third-party dataset rights remain with their authors. No new license is assigned to that data. Verify upstream attribution and redistribution terms before public publication.
The full input dataset is excluded from Git publication. Place a locally authorized copy at the documented input path before running.
