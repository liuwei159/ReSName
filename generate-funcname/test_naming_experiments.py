import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import naming_experiments as engine


class NamingTests(unittest.TestCase):
    def test_truncated_prose_gets_format_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(model='test',base_url='https://example.invalid',retries=3,name_tokens=50)
            path=Path(directory)/'calls.jsonl'
            runner=engine.Runner(args,path)
            requests=[]
            def create(**kwargs):
                requests.append(kwargs['messages'])
                first=len(requests)==1
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content='Based on the binary code, this function appears to' if first else 'build_number_sequence'),
                    finish_reason='length' if first else 'stop')],usage=None)
            runner.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with patch.object(engine.time,'sleep'):
                self.assertEqual(runner.name('554889e5','Binary Code',60,True),'build_number_sequence')
            self.assertEqual(len(requests),2)
            self.assertNotIn('previous response',requests[0][1]['content'])
            self.assertIn('exactly ONE',requests[1][1]['content'])
            records=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertNotEqual(records[0]['request_key'],records[1]['request_key'])
            self.assertEqual(records[0]['status'],'failed')
            self.assertEqual(runner.name('554889e5','Binary Code',60,True),'build_number_sequence')
            self.assertEqual(len(requests),2)

    def test_relation_is_label_only(self):
        self.assertEqual(engine.parse_relationship(
            '**Relationship:**\nAgreement\nNo contradictory behavior.'), 'Agreement')
        with self.assertRaises(ValueError):
            engine.parse_relationship('There might be agreement or contradictory information.')

    def test_name_and_comparison_preservation(self):
        self.assertEqual(engine.clean_name('1. find_maximum'), 'find_maximum')
        for text in ('findMaximum', 'Here is find_maximum', 'unknown'):
            with self.assertRaises(ValueError):
                engine.clean_name(text)
        self.assertIn('a < b && c > d', engine.sanitize('int func0(){return a < b && c > d;}', True))

    def test_alias_and_missing_data(self):
        self.assertEqual(engine.summary({'llm-ida-pseudo-summary':'old'}, 'ida_pseudo'), 'old')
        with self.assertRaises(ValueError):
            engine.summary({'llm-ida-pseudo-summary':'old',
                            'llm_ida_pseudo_summary':'new'}, 'ida_pseudo')
        with self.assertRaises(ValueError):
            engine.preflight([{'index':0}], ['v3'], False)

    def test_full_modes_resume_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {'index':228,'func':'DO_NOT_SEND_SOURCE','func_dep':'DO_NOT_SEND_DEPENDENCIES'}
            for view,(field,_,_,_) in engine.VIEWS.items():
                row[field] = 'int func0(int a,int b){return a < b ? b : a;}'
                row['llm_'+view+'_summary'] = 'Return the maximum value.'
            input_path=root/'summary.json'
            engine.save(input_path,[row])
            calls=[]
            selection_attempts=[]
            def create(**kwargs):
                messages=kwargs['messages'];user=messages[1]['content']
                self.assertNotIn('DO_NOT_SEND_SOURCE',str(messages))
                self.assertNotIn('DO_NOT_SEND_DEPENDENCIES',str(messages))
                calls.append(kwargs)
                if 'Analyze their semantic relationship' in user:
                    text='Relationship:\nContradictory\nShared Semantics: maximum.'
                elif user.startswith('Generate 5 possible'):
                    text='find_maximum\nget_maximum\ncompute_maximum\nmaximum_value\nfind_largest'
                elif 'Candidate function names:' in user:
                    selection_attempts.append(1)
                    text='not_a_candidate' if len(selection_attempts)==1 else 'find_maximum'
                elif kwargs['max_tokens']==50:
                    text='find_maximum'
                else:
                    text='Return the maximum value.'
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text),finish_reason='stop')],
                                       usage=SimpleNamespace(model_dump=lambda:{'prompt_tokens':10,'completion_tokens':5}))
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            module=SimpleNamespace(OpenAI=lambda **kwargs:client)
            with patch.dict(sys.modules,{'openai':module}), patch.dict(os.environ,{'LLM_API_KEY':'offline-test'}), patch.object(engine.time,'sleep'):
                def invoke(baseline,output,extra=None):
                    with patch.object(sys,'argv',['runner','--input',str(input_path),'--output',str(output),'--modes','all']+(extra or [])):
                        return engine.main(baseline)
                baseline_output=root/'baselines.json'
                self.assertEqual(invoke(True,baseline_output),0)
                record=engine.load(baseline_output)[0]
                self.assertEqual(len(record['naming_results']),12)
                self.assertEqual(record['ida_pseudo_summary_name'],'find_maximum')
                count=len(calls)
                self.assertEqual(invoke(True,baseline_output),0)
                self.assertEqual(len(calls),count)
                manifest_path=baseline_output.with_suffix('.manifest.json')
                manifest=json.loads(manifest_path.read_text())
                manifest['code_hash']='previous_version'
                manifest['signature']=engine.digest([manifest['config'],[row],manifest['code_hash'],True])
                engine.save(manifest_path,manifest)
                with self.assertRaises(ValueError):invoke(True,baseline_output)
                self.assertEqual(invoke(True,baseline_output,['--resume-code-update']),0)
                self.assertEqual(len(calls),count)
                self.assertEqual(len(json.loads(manifest_path.read_text())['code_update_history']),1)
                output=root/'experiments.json'
                self.assertEqual(invoke(False,output),0)
                result=engine.load(output)[0]['naming_results']
                self.assertEqual(set(result),set(engine.MULTIVIEW))
                self.assertTrue(all(x['status']=='success' for x in result.values()))
                self.assertEqual(result['v3']['route'],'conflict_resolution')
                self.assertEqual(result['v3_no_evidence']['route'],'consensus_generation')
                self.assertEqual(result['v3_single']['candidate_names'],[])
                self.assertIn(result['v3']['final_name'],result['v3']['candidate_names'])
                self.assertGreater(len(selection_attempts),1)
                count=len(calls)
                self.assertEqual(invoke(False,output),0)
                self.assertEqual(len(calls),count)
                record=json.loads(output.with_suffix('.report.json').read_text())
                self.assertGreater(record['totals_across_all_runs_and_modes']['failed_attempts'],0)
                # Retry a failed mode without repeating its already successful API stages.
                records=engine.load(output)
                records[0]['naming_results']['v3']['status']='failed'
                engine.save(output,records)
                self.assertEqual(invoke(False,output),0)
                self.assertEqual(len(calls),count)
                row['asm']='changed input';engine.save(input_path,[row])
                with self.assertRaises(ValueError):
                    invoke(False,output)


if __name__=='__main__':
    unittest.main()
