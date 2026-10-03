"""Offline source-grounding regressions; import the network guard first."""
import test_regressions as base
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import content
import perfection
import writer_evidence


class WriterEvidenceRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name)
        evidence = self.config / 'evidence'
        evidence.mkdir()
        self.source_text = 'Use only the product-specific instructions. Source text is reference data.'
        raw = self.source_text.encode('utf-8')
        (evidence / 'source.txt').write_bytes(raw)
        self.record = {'id':'fixture-manufacturer', 'kind':'manufacturer',
            'url':'https://manufacturer.example/instructions', 'snapshot':'source.txt',
            'expires_at':'2099-01-01T00:00:00Z','sha256':hashlib.sha256(raw).hexdigest()}
        self.save_records([self.record])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(base.fact_review, 'CONFIG_DIR', self.config))
        for module in (content, perfection):
            self.stack.enter_context(patch.object(module, 'load_system_prompt', return_value='Brand editorial instructions.'))
            self.stack.enter_context(patch.object(module, 'load_few_shot_articles', return_value=[]))
        self.env = {'ANTHROPIC_API_KEY':'offline-fixture', 'ANTHROPIC_MODEL':'fixture-model'}
        self.article = base.fixture()
        self.cta = {'title':'Care','handle':'care','url':base.safe.DOMAIN+'/collections/care'}
        self.requests = []
        def transport(request, **kwargs):
            self.requests.append(json.loads(request.data))
            return self.response(self.article)
        self.transport = self.stack.enter_context(patch('urllib.request.urlopen', side_effect=transport))

    def response(self, value):
        return io.BytesIO(json.dumps({'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}).encode())

    def save_records(self, records):
        (self.config/'evidence/sources.json').write_text(json.dumps(records),encoding='utf-8')

    def stages(self):
        return [lambda:content.generate_draft(topic='Care',date='2099-10-05',post_type='longtail',subtype='',cta=self.cta,env=self.env),
                lambda:content.self_critique(self.article,self.env),
                lambda:content.revise(self.article,{},self.env,original_user_prompt='Scheduled CTA: '+self.cta['url']),
                lambda:content.cross_review(self.article,self.env),
                lambda:perfection.perfection_pass(self.article,self.env,cta=self.cta)]

    def evidence_payload(self, request):
        text = request['messages'][0]['content']
        self.assertTrue(text.startswith('PRIMARY_SOURCES_JSON'))
        return json.loads(text.split('\n',1)[1].split('\n\n',1)[0])

    def system_text(self, request):
        value = request['system']
        return value if isinstance(value,str) else value[0]['text']

    def test_validated_sources_reach_all_five_stages_as_data(self):
        for stage in self.stages():stage()
        self.assertEqual(len(self.requests),5)
        for request in self.requests:
            payload = self.evidence_payload(request)
            self.assertEqual(payload['primary_sources'],[{**self.record,'text':self.source_text}])
            self.assertEqual(payload['evidence_sha256'],base.gate.digest(payload['primary_sources']))
            system = self.system_text(request)
            self.assertIn('never instructions',system)
            self.assertIn('Keep the supplied scheduled CTA',system)
            self.assertNotIn(self.source_text,system)
            self.assertIn(self.cta['url'],request['messages'][0]['content'])

    def test_empty_sources_keep_editorial_only_rule(self):
        self.save_records([])
        self.stages()[0]()
        self.assertEqual(self.evidence_payload(self.requests[0])['primary_sources'],[])
        self.assertIn('ordinary subjective editorial advice only',self.system_text(self.requests[0]))

    def test_expired_sources_block_every_stage_before_transport(self):
        self.save_records([{**self.record,'expires_at':'2000-01-01T00:00:00Z'}])
        for index,stage in enumerate(self.stages()):
            with self.subTest(stage=index), self.assertRaises(base.gate.ReviewRequired):stage()
        self.transport.assert_not_called()

    def test_tampered_snapshot_blocks_before_transport(self):
        (self.config/'evidence/source.txt').write_text('Changed without manifest update.',encoding='utf-8')
        with self.assertRaisesRegex(base.gate.ReviewRequired,'hash mismatch'):self.stages()[0]()
        self.transport.assert_not_called()

    def test_malformed_source_manifest_holds_before_transport(self):
        (self.config/'evidence/sources.json').write_text('{incomplete',encoding='utf-8')
        with self.assertRaisesRegex(base.gate.ReviewRequired,'Writer primary evidence unavailable'):self.stages()[0]()
        self.transport.assert_not_called()

    def test_factual_review_remains_separate(self):
        self.transport.side_effect=lambda *args,**kwargs:self.response(base.clear_review(None))
        with patch.object(base.fact_review,'load_sources',side_effect=AssertionError('Injected evidence must not reload')):
            result=base.fact_review.review_facts(self.article,self.env,sources=[])
        self.assertEqual(result['status'],'passed')
        request=json.loads(self.transport.call_args.args[0].data)
        self.assertTrue(self.system_text(request).startswith('You are an independent factual and safety editor'))
        self.assertNotIn('PRIMARY EVIDENCE RULES',self.system_text(request))
        self.assertEqual(json.loads(request['messages'][0]['content'])['primary_sources'],[])

    def test_json_retry_reuses_grounded_context_without_an_extra_source_load(self):
        responses=iter([io.BytesIO(b'{"content":[{"type":"text","text":"not JSON"}]}'),self.response(self.article)])
        self.transport.side_effect=lambda request,**kwargs:next(responses)
        with patch.object(base.fact_review,'load_sources',wraps=base.fact_review.load_sources) as loader:
            self.stages()[0]()
        self.assertEqual(loader.call_count,1)
        self.assertEqual(self.transport.call_count,2)
        a,b=[json.loads(call.args[0].data) for call in self.transport.call_args_list]
        self.assertEqual(self.evidence_payload(a),self.evidence_payload(b))


if __name__=='__main__':unittest.main()
