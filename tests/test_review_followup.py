"""Claude review regressions. Test imports install the network deny guard first."""
import test_regressions as base
import importlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import urllib.request

gate, safe, facts, shop = base.gate, base.safe, base.fact_review, base.shop


class ReviewFollowup(unittest.TestCase):
    def setUp(self):
        base.Regressions.setUp(self)

    def test_final_requires_complete_release_context(self):
        for context in (None, {}, {'date':'2099-10-05'}, {'cta_url':safe.DOMAIN+'/collections/care'},
                        {'date':'2099-02-30','cta_url':safe.DOMAIN+'/collections/care'},
                        {'date':'2099-10-05','cta_url':'/collections/care'},
                        {'date':'2099-10-05','cta_url':'https://user:pass@example.com/care'}):
            with self.subTest(context=context):
                a = base.fixture(True); a['_release_context'] = context
                with self.assertRaises(gate.ReviewRequired): gate.require_valid(a, final=True)

    def test_missing_context_fails_before_final_network(self):
        a = base.fixture(True); del a['_release_context']
        with patch.object(shop, '_http') as network, patch.object(safe, 'review_facts') as reviewer:
            with self.assertRaises(gate.ReviewRequired): safe.prepare_final(self.env, a, a['body_html'], a['featured_image_url'])
            network.assert_not_called(); reviewer.assert_not_called()

    def test_extra_cta_anchor_blocks_even_with_correct_destination(self):
        for extra in ('<a href="https://example.com/products/cup">Buy</a>', '<a>Empty</a>',
                      '<a href="'+safe.DOMAIN+'/collections/care">Duplicate</a>'):
            with self.subTest(extra=extra):
                a = base.fixture(True); a['body_html'] = a['body_html'].replace('Explore</a>', 'Explore</a>'+extra)
                with self.assertRaises(gate.ReviewRequired): gate.require_valid(a, final=True)

    def test_reference_links_outside_cta_are_allowed(self):
        a = base.fixture(True); a['body_html'] = '<p><a href="https://authority.example/guide">Source</a></p>'+a['body_html']
        self.assertTrue(gate.require_valid(a, final=True)['ok'])

    def test_empty_review_cannot_clear_salient_safety_language(self):
        statements = ('Microwave the sealed jar.', 'FDA-approved silicone is safe to 480°F.',
                      'Leave the warmer unattended.', 'This tea is caffeine-free.',
                      'Use dilution freely.', 'Flash point measures safe operating temperature.',
                      'This oil is safe for pets.', 'Children can use this candle.',
                      'Do not microwave a sealed jar.',
                      'The claim "microwave the sealed jar" is unsafe advice.')
        for statement in statements:
            with self.subTest(statement=statement):
                a = base.fixture(); a['body_html'] = '<p>'+statement+'</p>'
                with self.assertRaises(gate.ReviewRequired): facts.review_facts(a, {}, reviewer=base.clear_review, sources=[])
                self.assertIn('coverage_incomplete', a['fact_review']['issues'][0]['reason'])

    def test_supported_negation_and_rejected_quote_pass_contextual_review(self):
        for statement in ('Do not microwave a sealed jar.', 'The claim "microwave the sealed jar" is unsafe advice.'):
            with self.subTest(statement=statement):
                a = base.fixture(); a['body_html'] = '<p>'+statement+'</p>'
                source = {'id':'manufacturer', 'kind':'manufacturer', 'text':statement}
                review = {**base.clear_review(None), 'claims':[{'quote':statement, 'category':'safety',
                    'source_id':'manufacturer', 'source_excerpt':statement, 'support_explanation':'Manufacturer warns against this exact act.'}]}
                self.assertEqual(facts.review_facts(a, {}, reviewer=lambda _:review, sources=[source])['status'], 'passed')

    def test_ordinary_advice_and_incidental_words_do_not_trigger_tripwire(self):
        for text in ('Choose a fireside-inspired color.', 'Arrange your oven mitts by color.',
                     'Use a pet portrait as a decorative accent.', 'Choose a look you enjoy.'):
            with self.subTest(text=text):
                a = base.fixture(); a['body_html'] = '<p>'+text+'</p>'
                self.assertEqual(facts.review_facts(a, {}, reviewer=base.clear_review, sources=[])['status'], 'passed')

    def test_strict_json_rejects_repair_fences_duplicate_keys_and_constants(self):
        clean = json.dumps(base.clear_review(None))
        for text in (clean[:-1], clean[:-1]+',}', '```json\n'+clean+'\n```',
                     clean[:-1]+',"coverage_complete":true}', '{"value":NaN}'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError): facts.strict_review_json(text)
        self.assertEqual(facts.strict_review_json(clean), base.clear_review(None))

    def test_review_api_requires_normal_stop_reason(self):
        import content
        class Response:
            def __init__(self, reason): self.reason = reason
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'stop_reason':self.reason, 'content':[{'type':'text','text':json.dumps(base.clear_review(None))}]}).encode()
        for reason in ('max_tokens', 'stop_sequence', 'refusal', None):
            with self.subTest(reason=reason), patch.object(urllib.request, 'urlopen', return_value=Response(reason)):
                with self.assertRaises(RuntimeError): content._claude_call('mock', 'mock', facts.REVIEW_SYSTEM, [], require_complete=True)
                with self.assertRaises(gate.ReviewRequired):
                    facts.review_facts(base.fixture(), {'ANTHROPIC_API_KEY':'mock','ANTHROPIC_MODEL':'mock'}, sources=[])
        with patch.object(urllib.request, 'urlopen', return_value=Response('end_turn')):
            self.assertEqual(facts.strict_review_json(content._claude_call('mock', 'mock', facts.REVIEW_SYSTEM, [], require_complete=True)), base.clear_review(None))

    def test_real_fact_entrypoint_uses_strict_json_and_complete_response(self):
        import content
        env = {'ANTHROPIC_API_KEY':'mock', 'ANTHROPIC_MODEL':'mock'}
        with patch.object(content, '_claude_call', return_value=json.dumps(base.clear_review(None))[:-1]+',}') as call:
            with self.assertRaises(gate.ReviewRequired): facts.review_facts(base.fixture(), env, sources=[])
            self.assertIs(call.call_args.kwargs['require_complete'], True)

    def test_perfection_uses_only_scheduled_cta(self):
        import content, perfection
        cta = {'url':safe.DOMAIN+'/products/scheduled', 'title':'Scheduled product'}
        env = {'ANTHROPIC_API_KEY':'mock', 'ANTHROPIC_MODEL':'mock'}
        with patch.object(perfection, 'load_system_prompt', return_value='Brand rules'), patch.object(perfection, 'load_few_shot_articles', return_value=[]), patch.object(content, '_claude_call', return_value=json.dumps(base.fixture())) as call:
            perfection.perfection_pass(base.fixture(), env, cta=cta)
            sent = json.loads(call.call_args.kwargs['messages'][0]['content'])
            self.assertEqual(sent['scheduled_cta'], cta)
            self.assertNotIn('BEST-matching collection', call.call_args.kwargs['system'])
            self.assertIn('exactly one link', call.call_args.kwargs['system'])

    def test_perfection_without_schedule_fails_before_paid_call(self):
        import content, perfection
        with patch.object(content, '_claude_call') as paid:
            with self.assertRaises(gate.ReviewRequired): perfection.perfection_pass(base.fixture(), {})
            paid.assert_not_called()

    def test_legacy_maintenance_stops_before_loading_credentials(self):
        found = []
        for name in ('repair_images', 'revary_images', 'refine_images', 'review_and_upgrade'):
            if not (base.ROOT/'src'/f'{name}.py').exists(): continue
            module = importlib.import_module(name); found.append(name)
            with self.subTest(module=name), patch.object(module, 'load_env') as load:
                with self.assertRaises(gate.ReviewRequired): module.main()
                load.assert_not_called()
        if not found: self.skipTest('No legacy maintenance scripts in this repository')

    def test_legacy_workflow_schedule_removed_and_lock_aligned(self):
        workflows = [p for p in (base.ROOT/'.github/workflows').glob('*.yml') if p.stem in ('repair-images', 'revary-images', 'refine-images', 'review-upgrade')]
        if not workflows: self.skipTest('No legacy maintenance workflows in this repository')
        for path in workflows:
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding='utf-8')
                if base.ROOT.name.startswith(('steep-', 'sera-')): self.assertNotRegex(text, r'(?m)^\s+schedule:')
                self.assertIn('group: blog-publication-${{ github.repository }}', text)
                self.assertIn('workflow_dispatch:', text)

    def test_image_seo_migration_only_reports_candidates(self):
        if not (base.ROOT/'src/patch_img_seo.py').exists(): self.skipTest('No patch_img_seo script in this repository')
        import patch_img_seo as seo
        candidate = {'id':1,'title':'Existing','body_html':'<p><img src="https://cdn.example/1.webp" alt="One"></p>'}
        with patch.object(seo, 'load_env', return_value=self.env), patch.object(seo, 'get_blog_id', return_value=1), patch.object(seo, 'fetch_all', return_value=[candidate]), patch.object(seo, 'OUTPUT_DIR', Path(self.tmp.name)), patch.object(seo, 'shop_req') as request:
            report = seo.main()
            self.assertEqual(report['writes'], 0); self.assertEqual(len(report['candidates']), 1)
            request.assert_not_called()
            self.assertTrue((Path(self.tmp.name)/'image-seo-candidates.json').exists())

    def test_transient_link_status_does_not_become_confirmed_success(self):
        from contextlib import ExitStack
        for status in (403, 429):
            a = base.fixture(True)
            with self.subTest(status=status), ExitStack() as stack:
                if hasattr(shop, 'sanitize_body'): stack.enter_context(patch.object(shop, 'sanitize_body', side_effect=lambda env,body:body))
                network = stack.enter_context(patch.object(shop, '_http', return_value=(status,b'')))
                with self.assertRaises(gate.ReviewRequired): safe.prepare_final(self.env, a, a['body_html'], a['featured_image_url'])
                self.assertEqual(network.call_count, 1)

    def test_configured_model_is_required_and_honored(self):
        for value in (None, '', '   '):
            with self.subTest(value=value), self.assertRaises(gate.ReviewRequired):
                gate.configured_image_model({'IMAGEN_MODEL':value})
        self.assertEqual(gate.configured_image_model({'IMAGEN_MODEL':' gemini-chosen '}), 'gemini-chosen')
        with patch.object(base.images, 'generate_gemini_image', return_value=[b'png']) as generate, patch.object(base.images, 'optimize_to_webp', return_value=b'webp'), patch.object(base.images, 'verify_image_matches_prompt', return_value=(True,'matches')):
            base.images.generate_image_for_slot(prompt='Arrangement',filename_base='sample',api_key='mock',model='gemini-chosen',anthropic_key='mock')
            self.assertEqual(generate.call_args.kwargs['model'], 'gemini-chosen')

    def test_model_failure_does_not_silently_change_model(self):
        with patch.object(base.images, 'generate_gemini_image', side_effect=RuntimeError('Model unavailable')) as generate:
            with self.assertRaises(RuntimeError):
                base.images.generate_image_for_slot(prompt='Arrangement',filename_base='sample',api_key='mock',model='gemini-chosen',anthropic_key='mock')
            self.assertEqual(generate.call_count, 1)

    def test_sanitizer_does_not_strip_transient_failures(self):
        if not hasattr(shop, 'sanitize_body'): self.skipTest('No legacy sanitizer in this repository')
        import urllib.error
        a = base.fixture(True)
        for status in (403, 429):
            with self.subTest(status=status), patch.object(shop, '_URL_OK', {}), patch.object(shop, '_shop_info', return_value={'name':safe.BRAND,'domain':safe.DOMAIN.removeprefix('https://')}), patch.object(urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('https://fixture.example',status,'blocked',{},None)) as request:
                with self.assertRaises(RuntimeError): shop.sanitize_body(self.env, a['body_html'])
                self.assertEqual(request.call_count, 1)

    def test_prompt_does_not_require_perfect_scores_or_other_brand(self):
        import content
        source = (base.ROOT/'src/content.py').read_text(encoding='utf-8')
        system = (base.ROOT/'config/system_prompt.md').read_text(encoding='utf-8')
        self.assertNotIn('"score": 10', content.OUTPUT_SCHEMA_INSTRUCTION)
        self.assertNotIn('Mark 10/10 only if', source)
        self.assertNotIn('모든 차원 10점 만족', system)
        self.assertNotIn('concrete numbers in every post', source)
        if base.ROOT.name.startswith('sera-'): self.assertNotIn('Steep Society', content.CRITIQUE_SYSTEM)

    def test_repository_evidence_hashes_match_canonical_lf_files(self):
        import hashlib
        root = base.ROOT/'config/evidence'
        records = json.loads((root/'sources.json').read_text(encoding='utf-8'))
        for record in records:
            with self.subTest(source=record['id']):
                raw = (root/record['snapshot']).read_bytes()
                self.assertNotIn(b'\r\n', raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), record['sha256'])
        self.assertIn('config/evidence/*.txt text eol=lf', (base.ROOT/'.gitattributes').read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
