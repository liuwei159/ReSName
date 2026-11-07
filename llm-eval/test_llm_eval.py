import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import evaluate as gate


class GateTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(os.environ, {"LLM_MODEL": "test-model", "LLM_BASE_URL": "https://example.invalid"})
        self.config.start()
        self.addCleanup(self.config.stop)

    def fixtures(self):
        reference=[{'index':i,'func':'int func0(){return 1;}','language':lang} for i,lang in [(0,'c'),(4,'cpp')]]
        def records(modes):
            return [dict(row,naming_results={mode:{'status':'success','final_name':'return_one'} for mode in modes}) for row in reference]
        return reference,records(gate.BASELINES),records(gate.MULTIVIEW)

    def test_gate_excludes_whole_method(self):
        reference,baseline,multi=self.fixtures()
        baseline[1]['naming_results'][gate.BASELINES[0]]['status']='failed'
        report,predictions=gate.check_methods(reference,baseline,multi)
        self.assertEqual(len(predictions),len(gate.BASELINES)+len(gate.MULTIVIEW)-1)
        self.assertFalse(report['baseline:'+gate.BASELINES[0]]['ready'])
        self.assertEqual(report['baseline:'+gate.BASELINES[0]]['valid_count'],1)
        multi[0]['func']='changed source'
        report,predictions=gate.check_methods(reference,baseline,multi)
        self.assertFalse(any(k.startswith('multiview:') for k in predictions))

    def test_names_mapping_and_parser(self):
        candidates=gate.candidates_for(0,['a','b'],{'a':{gate.digest(0):'return_one'},'b':{gate.digest(0):'return_one'}},42)
        self.assertEqual(len(candidates),1)
        raw='**Function Name 0: return_one**\nC1: 5. Correct purpose.\nC2: 1. Valid format.'
        self.assertEqual(gate.parse_scores(raw,candidates)[0]['c1_score'],5)
        for value in (raw+'\n'+raw,raw.replace('return_one','wrong_name'),''):
            with self.assertRaises(ValueError):gate.parse_scores(value,candidates)

    def test_failure_skipped_then_retried(self):
        reference,baseline,multi=self.fixtures()
        calls=[]
        def create(**kwargs):
            calls.append(kwargs)
            names=re.findall(r'^Function Name (\d+): ([a-z_]+)$',kwargs['messages'][1]['content'],re.M)
            raw='invalid response' if len(calls)==1 else '\n'.join(f'**Function Name {i}: {name}**\nC1: 5. Correct purpose.\nC2: 1. Valid format.' for i,name in names)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw),finish_reason='stop')],usage=None)
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in [('reference',reference),('baseline',baseline),('multiview',multi)]:gate.save(root/(name+'.json'),value)
            argv=['gate','--reference',str(root/'reference.json'),'--baseline',str(root/'baseline.json'),'--multiview',str(root/'multiview.json'),'--output-dir',str(root/'eval'),'--expected-count','2','--retries','1','--evaluate']
            with patch.dict(sys.modules,{'openai':SimpleNamespace(OpenAI=lambda **kw:client)}),patch.dict(os.environ,{'LLM_API_KEY':'offline-test'}):
                with patch.object(sys,'argv',argv):self.assertEqual(gate.main(),1)
                self.assertEqual(len(calls),2)
                result=gate.load(root/'eval/evaluations.json')
                self.assertEqual([x['status'] for x in result],['failed','success'])
                self.assertEqual(len(json.loads((root/'eval/failed_samples.json').read_text())),1)
                self.assertEqual(json.loads((root/'eval/evaluation_summary.json').read_text())['averages'],{})
                with patch.object(sys,'argv',argv):self.assertEqual(gate.main(),0)
                self.assertEqual(len(calls),3)
                self.assertEqual(json.loads((root/'eval/failed_samples.json').read_text()),[])
                self.assertEqual(len((root/'eval/error_history.jsonl').read_text().splitlines()),1)
                self.assertTrue(json.loads((root/'eval/evaluation_summary.json').read_text())['complete'])

    def test_evaluation_and_resume(self):
        reference,baseline,multi=self.fixtures()
        calls=[]
        def create(**kwargs):
            user=kwargs['messages'][1]['content']
            self.assertNotIn('baseline:',user)
            self.assertNotIn('multiview:',user)
            names=re.findall(r'^Function Name (\d+): ([a-z_]+)$',user,re.M)
            self.assertEqual(len(names),1)
            calls.append(user)
            raw='\n'.join(f'**Function Name {i}: {name}**\nC1: 5. Correct purpose.\nC2: 1. Valid format.' for i,name in names)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw),finish_reason='stop')],usage=None)
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in [('reference',reference),('baseline',baseline),('multiview',multi)]:gate.save(root/(name+'.json'),value)
            argv=['gate','--reference',str(root/'reference.json'),'--baseline',str(root/'baseline.json'),
                  '--multiview',str(root/'multiview.json'),'--output-dir',str(root/'eval'),'--expected-count','2']
            with patch.object(sys,'argv',argv):self.assertEqual(gate.main(),0)
            self.assertFalse((root/'eval/evaluations.json').exists())
            with patch.dict(sys.modules,{'openai':SimpleNamespace(OpenAI=lambda **kw:client)}),patch.dict(os.environ,{'LLM_API_KEY':'offline-test'}):
                with patch.object(sys,'argv',argv+['--evaluate']):self.assertEqual(gate.main(),0)
                self.assertEqual(len(calls),2)
                summary=json.loads((root/'eval/evaluation_summary.json').read_text())
                self.assertTrue(summary['complete']);self.assertEqual(len(summary['averages']),len(gate.BASELINES)+len(gate.MULTIVIEW))
                with patch.object(sys,'argv',argv+['--evaluate']):self.assertEqual(gate.main(),0)
                self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main()
