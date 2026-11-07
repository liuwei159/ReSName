"""Gate complete naming methods, then optionally evaluate on the fixed dataset."""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import random
from pathlib import Path

# Shared naming utilities live in the sibling generate-funcname directory.
sys.path.append(str(Path(__file__).resolve().parent.parent / "generate-funcname"))

from naming_experiments import BASELINES, MULTIVIEW, Runner, clean_name, digest, load, save
from evaluation_prompts import build_eval_prompt, parse_llm_response


def check_methods(reference, baseline_rows, multi_rows):
    expected = {digest(x['index']):x for x in reference}
    report = {}
    predictions = {}
    for family, modes, rows in [('baseline', BASELINES, baseline_rows), ('multiview', MULTIVIEW, multi_rows)]:
        actual = {digest(x['index']):x for x in rows}
        for mode in modes:
            issues = []
            names = {}
            for key in actual.keys()-expected.keys():
                issues.append({'index':actual[key]['index'], 'reason':'unexpected sample'})
            for key, original in expected.items():
                row = actual.get(key)
                if row is None:
                    issues.append({'index':original['index'], 'reason':'missing sample'})
                    continue
                # Both naming runners preserve the original input fields.
                changed = [field for field,value in original.items() if row.get(field)!=value]
                if changed:
                    issues.append({'index':original['index'], 'reason':'input mismatch', 'fields':changed})
                    continue
                result = row.get('naming_results',{}).get(mode,{})
                if result.get('status') != 'success':
                    issues.append({'index':original['index'], 'reason':'missing/failed generation', 'error':result.get('error')})
                    continue
                try:
                    name = clean_name(result.get('final_name'))
                    if name != result.get('final_name'):
                        raise ValueError('Stored final name must be normalized.')
                    if result.get('candidate_names') and name not in result['candidate_names']:
                        raise ValueError('Final name is outside the candidate set.')
                except ValueError as error:
                    issues.append({'index':original['index'], 'reason':str(error)})
                    continue
                names[key] = name
            method = family + ':' + mode
            report[method] = {'ready':not issues, 'valid_count':len(names),
                              'expected_count':len(reference), 'issues':issues}
            if not issues:
                predictions[method] = names
    return report, predictions


def candidates_for(index, methods, predictions, seed):
    # Identical names receive one score, shared across every method producing it.
    grouped = {}
    for method in methods:
        name = predictions[method][digest(index)]
        grouped.setdefault(name,[]).append(method)
    names = list(grouped)
    shuffle_seed = int(hashlib.sha256(f'{seed}:{digest(index)}'.encode()).hexdigest(),16)
    random.Random(shuffle_seed).shuffle(names)
    return [{'function_id':i, 'function_label':f'Function Name {i}',
             'function_name':name, 'methods':grouped[name]} for i,name in enumerate(names)]


def parse_scores(raw, candidates):
    results = parse_llm_response(raw)
    expected = {x['function_id']:x['function_name'] for x in candidates}
    if len(results)!=len(expected) or {x['function_id'] for x in results}!=set(expected):
        raise ValueError('Incomplete, duplicate or unexpected evaluation IDs.')
    for result in results:
        name = result['function_name_in_response'].strip('*` \t\r\n')
        if name != expected[result['function_id']]:
            raise ValueError('Judge changed a candidate name or its ID mapping.')
        if not result['c1_explanation'] or not result['c2_explanation']:
            raise ValueError('Judge omitted a score explanation.')
    return results


def write_csv(path, rows):
    text = io.StringIO()
    writer = csv.DictWriter(text,fieldnames=['index','language','method','function_name','C1','C2'])
    writer.writeheader()
    writer.writerows(rows)
    Path(path).write_text(text.getvalue(),encoding='utf-8-sig')


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=root.parent/'results_317/summary/summary.json')
    parser.add_argument('--baseline',type=Path,default=root.parent/'results_317/naming/baselines.json')
    parser.add_argument('--multiview',type=Path,default=root.parent/'results_317/naming/multiview.json')
    parser.add_argument('--output-dir',type=Path,default=root.parent/'results_317/evaluation')
    parser.add_argument('--expected-count',type=int,default=317)
    parser.add_argument('--methods',default='ready',help='ready, or comma-separated family:mode IDs')
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--evaluate',action='store_true',help='Make judge API requests after the gate.')
    parser.add_argument('--model',default=os.environ.get('LLM_MODEL'))
    parser.add_argument('--base-url',default=os.environ.get('LLM_BASE_URL'))
    parser.add_argument('--retries',type=int,default=3)
    parser.add_argument('--judge-tokens',type=int,default=12000)
    args=parser.parse_args()
    if args.expected_count<1 or args.retries<1 or args.judge_tokens<1:
        parser.error('Counts, retries and token budget must be positive.')
    reference=load(args.reference)
    if len(reference)!=args.expected_count:
        raise ValueError(f'Expected {args.expected_count} reference samples, found {len(reference)}.')
    if any(not isinstance(x.get('func'),str) or not x['func'].strip() for x in reference):
        raise ValueError('Every reference sample needs source code for evaluation.')
    baseline=load(args.baseline) if args.baseline.exists() else []
    multiview=load(args.multiview) if args.multiview.exists() else []
    report,predictions=check_methods(reference,baseline,multiview)
    conflict_ids = None
    if 'multiview:v3_single' in predictions:
        relations = {row['index']: row['naming_results']['v3_single'].get('relationship')
                     for row in multiview if 'v3_single' in row.get('naming_results', {})}
        if all(relations.get(row['index']) in ('Agreement', 'Complementary', 'Contradictory')
               for row in reference):
            conflict_ids = {row['index'] for row in reference
                            if relations[row['index']] == 'Contradictory'}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    # Generation must not be writing these snapshots during evaluation.
    save(args.output_dir/'completeness_report.json',{
        'expected_count':len(reference),'reference_digest':digest(reference),
        'criteria':'All fixed samples, matching input, success status and valid name. No semantic score filtering.',
        'methods':report})
    for method,result in report.items():
        print(f"{method}: {'READY' if result['ready'] else 'BLOCKED'} ({result['valid_count']}/{len(reference)})")
    if not args.evaluate:
        print('Check complete; no API calls. Add --evaluate to score ready methods.')
        return 0
    methods=list(predictions) if args.methods=='ready' else list(dict.fromkeys(args.methods.split(',')))
    if not methods or any(method not in predictions for method in methods):
        raise ValueError('No ready methods, or a requested method failed the gate.')
    if not os.environ.get('LLM_API_KEY'):
        raise RuntimeError('Set LLM_API_KEY before evaluation.')
    config={'reference':reference,'methods':methods,'predictions':predictions,
            'seed':args.seed,'model':args.model,'base_url':args.base_url,'judge_tokens':args.judge_tokens,
            'code':Path(__file__).read_text(encoding='utf-8'),
            'prompt_code':(root/'evaluation_prompts.py').read_text(encoding='utf-8')}
    signature=digest(config)
    manifest=args.output_dir/'evaluation_manifest.json'
    output=args.output_dir/'evaluations.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8'))['signature']!=signature:
            raise ValueError('Evaluation inputs/settings/methods changed; use a new --output-dir.')
    elif output.exists() or (args.output_dir/'judge.calls.jsonl').exists():
        raise ValueError('Existing evaluation artifacts have no manifest; use a new output directory.')
    else:
        save(manifest,{'signature':signature,'methods':methods,'seed':args.seed,'model':args.model,
                       'reference_digest':digest(reference),'judge_tokens':args.judge_tokens})
    existing={digest(x['index']):x for x in load(output)} if output.exists() else {}
    results=[existing.get(digest(x['index']),{'index':x['index'],'status':'pending'}) for x in reference]
    runner=Runner(args,args.output_dir/'judge.calls.jsonl')
    for position,(item,result) in enumerate(zip(reference,results),1):
        if result.get('status')=='success':
            continue
        runner.trace=[]
        try:
            candidates=candidates_for(item['index'],methods,predictions,args.seed)
            prompt=build_eval_prompt(item['func'].strip(),candidates)
            raw=runner.call(item['index'],'EVALUATION',
                            'Evaluate candidate names against the supplied source code.',
                            prompt,args.judge_tokens,lambda text:parse_scores(text,candidates))
            results[position-1]={'index':item['index'],'language':item.get('language'),
                                 'status':'success','candidates':candidates,
                                 'results':parse_scores(raw,candidates),'raw_response':raw}
        except Exception as error:
            failure={'index':item['index'], 'status':'failed', 'error':str(error),
                     'error_type':type(error).__name__, 'failed_at':time.strftime('%Y-%m-%d %H:%M:%S'),
                     'methods':methods, 'call_keys':list(runner.trace)}
            results[position-1]=failure
            with (args.output_dir/'error_history.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(failure,ensure_ascii=False)+'\n')
                stream.flush()
                os.fsync(stream.fileno())
            print(f"Skipping failed index={item['index']}: {error}",flush=True)
        save(output,results)
        print(f"[{position}/{len(reference)}] index={item['index']} {results[position-1]['status']}",flush=True)
    details=[]
    for item in results:
        if item.get('status')!='success':continue
        scores={x['function_id']:x for x in item['results']}
        for candidate in item['candidates']:
            score=scores[candidate['function_id']]
            for method in candidate['methods']:
                details.append({'index':item['index'],'language':item.get('language'),
                                'method':method,'function_name':candidate['function_name'],
                                'C1':score['c1_score'],'C2':score['c2_score']})
    save(args.output_dir/'failed_samples.json',[x for x in results if x.get('status')=='failed'])
    write_csv(args.output_dir/'scores.csv',details)
    complete=all(x.get('status')=='success' for x in results)
    averages={}
    if complete:
        for method in methods:
            averages[method]={}
            method_scores = [row for row in details if row['method'] == method]
            subsets = {'all': method_scores}
            for language in ('c', 'cpp'):
                subsets[language] = [row for row in method_scores if row['language'] == language]
            if conflict_ids is not None:
                subsets['conflict'] = [row for row in method_scores if row['index'] in conflict_ids]
            for group, subset in subsets.items():
                if subset:
                    averages[method][group]={'count':len(subset),
                        'C1_mean':sum(x['C1'] for x in subset)/len(subset),
                        'C2_mean':sum(x['C2'] for x in subset)/len(subset)}
    save(args.output_dir/'evaluation_summary.json',{'complete':complete,
        'evaluated_samples':sum(x.get('status')=='success' for x in results),
         'expected_samples':len(reference),'averages':averages,
        'conflict_subset':{
            'defined_by':'multiview:v3_single relationship=Contradictory',
            'count':len(conflict_ids) if conflict_ids is not None else None,
            'indices':sorted(conflict_ids, key=str) if conflict_ids is not None else None,
        },
        'note':'Final means are withheld until every fixed sample has a complete evaluation.'})
    failed_count=sum(x.get('status')=='failed' for x in results)
    print(f'Finished: success={len(reference)-failed_count}, failed={failed_count}. Rerun the same command to retry failures.')
    print(f'Results: {args.output_dir.resolve()}')
    return 0 if complete else 1


if __name__=='__main__':
    raise SystemExit(main())
