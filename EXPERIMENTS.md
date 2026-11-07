# Experiment workflow

Run commands from the repository root. Set LLM_API_KEY, LLM_MODEL and LLM_BASE_URL locally. Optional LLM_EXTRA_BODY contains JSON for server extensions.

## Data
Input: src_data_add_binary_code_v2/data_all_unique_O0_with_binary.json.
328 O0 records minus 11 source duplicates yield 317. All are used, without random subsampling or language balancing.

Extract from an upstream JSON with:
    python read_src_data_v2/read_random_200_O0_sample_v2.py --input /path/to/humaneval-decompile.json

## Summaries and naming
    python generate-summary/generate-summary.py
    python generate-funcname/generate-funcname.py --input results_317/summary/summary.json --output results_317/naming/baselines.json
    python generate-funcname/generate-funcname-multiple-views-v3.py --input results_317/summary/summary.json --output results_317/naming/multiview.json --modes all

The multi-view entry defaults to v3_single. Add --dry-run to naming commands for input checks.

## Evaluation
    python llm-eval/evaluate.py
    python llm-eval/evaluate.py --evaluate

Check required methods are READY (317/317) first. Evaluation shuffles anonymous unique names against source code. Identical names are scored once per sample. C1 is semantic suitability from 1 to 5; C2 is naming format. Failures are recorded for retry. Final means require complete evaluation.

## Tables
1. Direct versus summary naming for six representations.
2. Proposed v3_single versus the strongest IDA pseudocode and strongest Ghidra pseudocode configurations.
3. v3_single versus v3_single_no_evidence and simple_fusion_single, with single-name generation fixed.

No-evidence preserves relation classification and branch routing, removing pseudocode inputs during conflict resolution. Simple fusion always uses the generic summary fusion prompt. All methods use the conflict subset defined by v3_single.

Aggregate groups are all, c, cpp and conflict. Keep settings fixed for resume; changed experiments require a new output directory.

## Final results
results/generated_names.json contains final names, status and relationships.
results/scores.csv contains per-sample scores.
results/evaluation_summary.json contains overall, language and conflict-subset means.
