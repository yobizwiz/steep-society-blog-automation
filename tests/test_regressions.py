"""Offline regressions: all network access is denied before project imports."""
import ast
import copy
import importlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def denied(*args, **kwargs):
    raise AssertionError('Network access is forbidden in offline tests')


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied
urllib.request.urlopen = denied

import validators
import release_gate as gate
import safe_publish as safe
import shopify_pub as shop
import fact_review
import images
import weekly


def fixture(final=False, color='#faf7f1', recap='Quick Recap'):
    url = safe.DOMAIN + '/collections/care'
    body = ('<div style="background:#faf7f1"><p>A calm arrangement is a matter of preference.</p></div>'
            '<table style="background:#faf7f1"><tr><td>Choose your preferred look.</td></tr></table>'
            '<!-- IMG:body-1 --><!-- IMG:body-2 -->'
            f'<h2>{recap}</h2><ul><li>Choose a look you enjoy.</li></ul>'
            f'<div data-cta="primary" style="background:{color}"><p>Explore the collection.</p>'
            f'<a href="{url}" role="button">Explore</a></div>')
    article = {'title':'A calm arrangement', 'body_html':body, 'summary':'Choose a look you enjoy.',
               'url_slug':'calm-arrangement', 'meta_title':'A calm arrangement for your everyday space',
               'meta_description':'Explore ways to arrange your everyday space around the look you enjoy. Compare personal preferences and choose a setting that feels comfortable to you.',
               'tags':['care'], 'images':[{'role':r,'alt':'Simple arrangement','prompt':'An arrangement', 'filename':'arrangement-'+str(i)}
                  for i,r in enumerate(['featured','body','body'])],
               'internal_judgment':{k:{'score':8} for k in gate.DIMENSIONS},
               '_release_context':{'date':'2099-10-05','post_type':'longtail','cta_url':url}}
    if final:
        article['body_html'] = shop.insert_body_images(body, [{'url':f'https://cdn.example/{i}.webp','alt':'Simple arrangement'} for i in (1,2)])
        article['featured_image_url'] = 'https://cdn.example/hero.webp'
        article['uploaded_images'] = [{'role': 'featured', 'url': article['featured_image_url'], 'vision_verified': True}] + [
            {'role': 'body', 'url': f'https://cdn.example/{i}.webp', 'vision_verified': True} for i in (1, 2)]
    return article


def clear_review(_):
    return {'coverage_complete':True,'cta_relevant':True,'claims':[],'issues':[]}


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for module in (gate, safe, weekly):
            p = patch.object(module, 'OUTPUT_DIR', Path(self.tmp.name))
            p.start(); self.addCleanup(p.stop)
        self.env = {'SHOPIFY_STORE_URL':'fixture.myshopify.com','SHOPIFY_BLOG_HANDLE':safe.BLOG_HANDLE}
        self.entry = {'title':'A calm arrangement','type':'longtail','cta_collection':'care'}
        self.cols = {'care':{'title':'Explore','url':safe.DOMAIN+'/collections/care','handle':'care'}}

    def test_all_modules_parse_and_import_without_network(self):
        for path in (ROOT/'src').glob('*.py'):
            ast.parse(path.read_text(encoding='utf-8'))
            importlib.import_module(path.stem)

    def test_semantic_cta_ignores_quick_answer_and_table_colors(self):
        self.assertTrue(validators.validate(fixture())['ok'])

    def test_mera_color_and_recap_case_are_valid(self):
        self.assertTrue(validators.validate(fixture(color='#f7f4ef', recap='QUICK RECAP'))['ok'])

    def test_legacy_button_works_without_marker(self):
        a = fixture()
        a['body_html'] = a['body_html'].replace(' data-cta="primary"','')
        self.assertTrue(validators.validate(a)['ok'])

    def test_background_without_button_is_not_cta(self):
        a = fixture(); a['body_html'] = '<h2>Quick Recap</h2><div style="background:#faf7f1">text</div>'
        self.assertIn('cta_count',[v['rule'] for v in validators.validate(a)['violations']])

    def test_product_anywhere_does_not_satisfy_expected_cta(self):
        a = fixture(); a['_release_context']['cta_url'] = safe.DOMAIN+'/products/spatula'
        a['body_html'] = '<a href="'+safe.DOMAIN+'/products/cup">Cup</a>'+a['body_html']
        self.assertIn('cta_destination',[v['rule'] for v in validators.validate(a)['violations']])

    def test_trailing_content_blocks_but_json_ld_is_allowed(self):
        a = fixture(); a['body_html'] += '<p>Extra</p>'
        self.assertFalse(validators.validate(a)['ok'])
        a = fixture(); a['body_html'] += '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        self.assertTrue(validators.validate(a)['ok'])

    def test_meta_measurements_ignore_self_report(self):
        a = fixture(); a['meta_title']='x'*80; a['meta_title_length']=50
        result = validators.validate(a)
        self.assertEqual(result['measured']['meta_title'],80)
        self.assertTrue(result['warnings'])

    def test_missing_image_marker_blocks(self):
        a=fixture(); a['body_html']=a['body_html'].replace('<!-- IMG:body-2 -->','')
        with self.assertRaises(gate.ReviewRequired): gate.require_valid(a)

    def test_final_images_revalidated(self):
        self.assertTrue(validators.validate(fixture(True),final=True)['ok'])
        self.assertFalse(validators.validate(fixture(),final=True)['ok'])

    def test_schema_uses_uploaded_image(self):
        a=fixture(True)
        a['body_html'] += '<script type="application/ld+json">{"@graph":[{"@type":"BlogPosting","image":"https://invented/image.jpg"}]}</script>'
        a['body_html']=safe.sync_schema_images(a['body_html'],a['featured_image_url'])
        self.assertNotIn('invented',a['body_html'])
        self.assertTrue(validators.validate(a,final=True)['ok'])

    def test_low_or_incomplete_scores_cannot_pass(self):
        for value in (7, '10', None, True):
            a=fixture(); a['internal_judgment']['eeat']['score']=value
            with self.assertRaises(gate.ReviewRequired): gate.require_valid(a)

    def test_normal_advice_clears_automatic_fact_review(self):
        a=fixture()
        self.assertEqual(fact_review.review_facts(a,{},reviewer=clear_review,sources=[])['status'],'passed')

    def test_source_absence_holds_specific_claim(self):
        def result(_): return {'coverage_complete':True,'cta_relevant':True,'claims':[],
                               'issues':[{'quote':'Microwave a sealed jar','reason':'No manufacturer evidence'}]}
        with self.assertRaises(gate.ReviewRequired): fact_review.review_facts(fixture(),{},reviewer=result,sources=[])

    def test_source_quote_and_claim_must_match(self):
        claim={'quote':'A calm arrangement','source_id':'s1','source_excerpt':'not in source','support_explanation':'test'}
        def result(_): return {**clear_review(_),'claims':[claim]}
        with self.assertRaises(gate.ReviewRequired):
            fact_review.review_facts(fixture(),{},reviewer=result,sources=[{'id':'s1','text':'different'}])

    def test_supported_claim_can_clear(self):
        claim={'quote':'A calm arrangement','source_id':'s1','source_excerpt':'Support','support_explanation':'Matching source'}
        result=lambda _: {**clear_review(_),'claims':[claim]}
        self.assertEqual(fact_review.review_facts(fixture(),{},reviewer=result,sources=[{'id':'s1','text':'Support'}])['status'],'passed')

    def test_fact_reviewer_error_and_malformed_coverage_hold(self):
        for result in ({}, {'coverage_complete':False}):
            with self.assertRaises(gate.ReviewRequired): fact_review.review_facts(fixture(),{},reviewer=lambda _:result,sources=[])

    def test_fact_fingerprint_changes_with_final_cta(self):
        a=fixture(); first=fact_review.review_facts(a,{},reviewer=clear_review,sources=[])['input_sha256']
        a['body_html']=a['body_html'].replace('Explore the collection.','The product holds 30 pounds.')
        self.assertNotEqual(first,fact_review.review_facts(a,{},reviewer=clear_review,sources=[])['input_sha256'])

    def test_missing_schedule_is_not_already_exists(self):
        self.assertEqual(weekly.process_one_day('2099-10-05',self.env,{},self.cols)['status'],'missing_schedule')

    def test_duplicate_lookup_failure_never_generates(self):
        with patch.object(weekly,'find_article_by_publish_date',side_effect=RuntimeError('lookup failed')), patch.object(weekly,'generate_full_article') as generate:
            self.assertEqual(weekly.process_one_day('2099-10-05',self.env,{'2099-10-05':self.entry},self.cols)['status'],'failed')
            generate.assert_not_called()

    def test_existing_date_is_successful_skip(self):
        with patch.object(weekly,'find_article_by_publish_date',return_value={'id':'1','handle':'existing'}), patch.object(weekly,'generate_full_article') as generate:
            self.assertEqual(weekly.process_one_day('2099-10-05',self.env,{'2099-10-05':self.entry},self.cols)['status'],'already_exists')
            generate.assert_not_called()

    def test_failed_validation_prevents_paid_images_and_publish(self):
        a=fixture(); a['body_html']='<h1>Invalid</h1>'
        with patch.object(weekly,'find_article_by_publish_date',return_value=None), patch.object(weekly,'generate_full_article',return_value=a), patch.object(weekly,'generate_image_for_slot') as gen, patch.object(weekly,'create_article') as publish:
            result=weekly.process_one_day('2099-10-05',self.env,{'2099-10-05':self.entry},self.cols)
            self.assertEqual(result['status'],'review_required'); gen.assert_not_called(); publish.assert_not_called()

    def test_paginated_duplicate_lookup_is_blog_scoped(self):
        def gql(env,q,v):
            if 'query Blogs' in q: return {'blogs':{'nodes':[{'id':'gid://shopify/Blog/1','handle':safe.BLOG_HANDLE}],'pageInfo':{'hasNextPage':False,'endCursor':None}}}
            nodes=[] if v['after'] is None else [{'id':'gid://shopify/Article/9','title':'Late','handle':'late','publishedAt':'2099-10-05T07:00:00Z','isPublished':False}]
            return {'blog':{'id':v['id'],'handle':safe.BLOG_HANDLE,'articles':{'nodes':nodes,'pageInfo':{'hasNextPage':v['after'] is None,'endCursor':'next'}}}}
        with patch.object(shop,'_gql',side_effect=gql): self.assertEqual(safe.find_article_by_publish_date(self.env,'2099-10-05')['handle'],'late')

    def test_missing_pagination_fails_closed(self):
        with patch.object(shop,'_gql',return_value={'blogs':{'nodes':[]}}):
            with self.assertRaises(RuntimeError): safe.get_blog_id(self.env,safe.BLOG_HANDLE)

    def test_local_lock_excludes_second_writer(self):
        with gate.publication_lock(self.env):
            with self.assertRaises(gate.ReviewRequired):
                with gate.publication_lock(self.env): pass

    def test_vision_network_failure_is_false(self):
        matched,_=images.verify_image_matches_prompt(b'webp','prompt',anthropic_key='mock')
        self.assertIs(matched,False)

    def test_vision_requires_key_before_generation(self):
        with self.assertRaises(RuntimeError):
            images.generate_image_for_slot(prompt='x',filename_base='x',api_key='mock',model='mock')

    def test_vision_retry_exhaustion_never_returns_bad_image(self):
        generate_name='generate_gemini_image' if hasattr(images,'generate_gemini_image') else 'generate_imagen'
        with patch.object(images,generate_name,return_value=[b'png']), patch.object(images,'optimize_to_webp',return_value=b'webp'), patch.object(images,'verify_image_matches_prompt',return_value=(False,'mismatch')):
            with self.assertRaises(RuntimeError): images.generate_image_for_slot(prompt='x',filename_base='x',api_key='mock',model='gemini-test',anthropic_key='mock',max_vision_retries=1)

    def test_schedule_user_errors_preserve_receipt_and_fail(self):
        a=fixture(True)
        with patch.object(safe,'prepare_final',return_value=a), patch.object(safe,'_articles',return_value=iter([])), patch.object(shop,'_api',return_value={'article':{'id':1,'handle':a['url_slug']}}) as post, patch.object(shop,'_gql',return_value={'articleUpdate':{'article':None,'userErrors':[{'message':'Denied'}]}}):
            with self.assertRaises(RuntimeError): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'],publish_mode='scheduled',scheduled_at='2099-10-05T07:00:00Z')
            receipt=json.loads(next((Path(self.tmp.name)/'receipts').glob('*.json')).read_text())
            self.assertEqual(receipt['status'],'reconciliation_required'); self.assertEqual(receipt['article_id'],1)
            with self.assertRaises(gate.ReviewRequired): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'])
            self.assertEqual(post.call_count,1)

    def test_update_capture_failure_has_no_write(self):
        with patch.object(shop,'_gql',side_effect=RuntimeError('capture failed')) as gql, patch.object(shop,'_api') as api:
            with self.assertRaises(RuntimeError): safe.update_article_body(self.env,1,fixture(True)['body_html'],article=fixture(True))
            api.assert_not_called(); self.assertEqual(gql.call_count,1)

    def test_final_sanitization_removing_cta_blocks_publish(self):
        if not hasattr(shop,'sanitize_body'): self.skipTest('No legacy sanitizer in this repository')
        a=fixture(True)
        with patch.object(shop,'sanitize_body',return_value='<h2>Quick Recap</h2><p>No CTA remains.</p>'), patch.object(safe,'review_facts') as facts:
            with self.assertRaises(gate.ReviewRequired): safe.prepare_final(self.env,a,a['body_html'],a['featured_image_url'])
            facts.assert_not_called()
            self.assertTrue(list(Path(self.tmp.name).glob('*-final.check.json')))

    def test_public_url_uses_correct_blog(self):
        self.assertIn('/blogs/'+safe.BLOG_HANDLE+'/',safe.public_url('test'))

    def test_text_extraction_preserves_negation_and_inline_order(self):
        a=fixture(); a['body_html']='<p>Do not <strong>microwave</strong> a sealed jar.</p>'
        self.assertEqual(fact_review.factual_payload(a)['text'],'Do not microwave a sealed jar.')

    def test_source_snapshot_integrity_and_expiry(self):
        import hashlib
        evidence=Path(self.tmp.name)/'evidence'; evidence.mkdir()
        (evidence/'source.txt').write_text('Supported source text',encoding='utf-8')
        record={'id':'s','url':'https://authority.example/page','kind':'primary_authority','snapshot':'source.txt',
                'sha256':hashlib.sha256(b'Supported source text').hexdigest(),'expires_at':'2099-01-01T00:00:00Z'}
        with patch.object(fact_review,'CONFIG_DIR',Path(self.tmp.name)):
            (evidence/'sources.json').write_text(json.dumps([record]))
            self.assertEqual(len(fact_review.load_sources()),1)
            (evidence/'source.txt').write_text('Tampered')
            with self.assertRaises(gate.ReviewRequired): fact_review.load_sources()
            record['expires_at']='2000-01-01T00:00:00Z'
            (evidence/'sources.json').write_text(json.dumps([record]))
            with self.assertRaises(gate.ReviewRequired): fact_review.load_sources()

    def test_vision_invalid_json_and_string_false_do_not_pass(self):
        class Response:
            def __init__(self,text): self.text=text
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return json.dumps({'content':[{'type':'text','text':self.text}]}).encode()
        for raw in ('not json','{"match":"false"}','{"reason":"missing boolean"}'):
            with patch.object(urllib.request,'urlopen',return_value=Response(raw)):
                self.assertIs(images.verify_image_matches_prompt(b'webp','prompt',anthropic_key='mock')[0],False)

    def test_passing_vision_returns_verified_flag(self):
        generate_name='generate_gemini_image' if hasattr(images,'generate_gemini_image') else 'generate_imagen'
        with patch.object(images,generate_name,return_value=[b'png']), patch.object(images,'optimize_to_webp',return_value=b'webp'), patch.object(images,'verify_image_matches_prompt',return_value=(True,'matching')):
            self.assertIs(images.generate_image_for_slot(prompt='x',filename_base='x',api_key='mock',model='gemini-test',anthropic_key='mock')['vision_verified'],True)

    def test_schedule_success_rechecks_and_saves_final_exact_html(self):
        a=fixture(True)
        def gql(env,q,v):
            if 'mutation' in q:
                return {'articleUpdate':{'article':{'id':'gid://shopify/Article/1'},'userErrors':[]}}
            return {'article':{'id':'gid://shopify/Article/1','body':a['body_html'],'isPublished':False,'publishedAt':'2099-10-05T07:00:00+00:00'}}
        with patch.object(safe,'prepare_final',return_value=a), patch.object(safe,'_articles',return_value=iter([])) as duplicates, patch.object(shop,'_api',return_value={'article':{'id':1,'handle':a['url_slug']}}) as post, patch.object(shop,'_gql',side_effect=gql):
            out=safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'],publish_mode='scheduled',scheduled_at='2099-10-05T07:00:00Z')
            self.assertEqual(out['id'],1); duplicates.assert_called_once()
            self.assertEqual(post.call_args.kwargs['body']['article']['body_html'],a['body_html'])
            self.assertIs(post.call_args.kwargs['body']['article']['published'],False)

    def test_schedule_readback_mismatch_fails(self):
        a=fixture(True)
        def gql(env,q,v):
            if 'mutation' in q: return {'articleUpdate':{'article':{'id':'gid://shopify/Article/1'},'userErrors':[]}}
            return {'article':{'body':a['body_html'],'isPublished':True,'publishedAt':'2099-10-05T07:00:00Z'}}
        with patch.object(safe,'prepare_final',return_value=a), patch.object(safe,'_articles',return_value=iter([])), patch.object(shop,'_api',return_value={'article':{'id':1,'handle':a['url_slug']}}), patch.object(shop,'_gql',side_effect=gql):
            with self.assertRaises(RuntimeError): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'],publish_mode='scheduled',scheduled_at='2099-10-05T07:00:00Z')

    def test_handle_conflict_does_not_create_suffixed_duplicate(self):
        a=fixture(True)
        with patch.object(safe,'prepare_final',return_value=a), patch.object(safe,'_articles',return_value=iter([{'id':'existing','handle':a['url_slug'],'publishedAt':None}])), patch.object(shop,'_api') as post:
            with self.assertRaises(gate.ReviewRequired): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'])
            post.assert_not_called()

    def test_schedule_requires_future_timestamp_before_any_write(self):
        with patch.object(shop,'_api') as post:
            for when in (None,'2020-01-01T00:00:00Z','2099-01-01T00:00:00'):
                with self.assertRaises(ValueError): safe.create_article(self.env,blog_id=1,article=fixture(),featured_image_url='https://cdn.example/a.webp',featured_image_alt='Hero',body_html='body',publish_mode='scheduled',scheduled_at=when)
            post.assert_not_called()

    def test_successful_body_update_is_one_atomic_graphql_write(self):
        a=fixture(True); current={'id':'gid://shopify/Article/1','body':'old','isPublished':False,'publishedAt':'2099-10-05T07:00:00Z','blog':{'handle':safe.BLOG_HANDLE}}
        responses=[{'article':current},{'articleUpdate':{'article':{'id':current['id']},'userErrors':[]}},{'article':{**current,'body':a['body_html']}}]
        with patch.object(safe,'prepare_final',return_value=a), patch.object(shop,'_gql',side_effect=responses) as gql, patch.object(shop,'_api') as rest:
            safe.update_article_body(self.env,1,a['body_html'],article=a)
            rest.assert_not_called()
            fields=gql.call_args_list[1].args[2]['article']
            self.assertEqual(fields['publishDate'],current['publishedAt']); self.assertIs(fields['isPublished'],False)
            self.assertEqual(fields['body'],a['body_html'])

    def test_final_fact_review_sees_actual_post_transform_html(self):
        a=fixture(True)
        from contextlib import ExitStack
        with ExitStack() as stack:
            if hasattr(shop,'sanitize_body'): stack.enter_context(patch.object(shop,'sanitize_body',side_effect=lambda env,body:body.replace('Explore the collection.','See this collection.')))
            stack.enter_context(patch.object(shop,'_http',return_value=(200,b'')))
            check=stack.enter_context(patch.object(safe,'review_facts',return_value={'status':'passed'}))
            out=safe.prepare_final(self.env,a,a['body_html'],a['featured_image_url'])
            self.assertEqual(check.call_args.args[0]['body_html'],out['body_html'])
            saved=json.loads(next(Path(self.tmp.name).glob('*-final.json')).read_text())
            self.assertEqual(saved['body_html'],out['body_html'])

    def test_internal_link_failure_blocks_final(self):
        a=fixture(True)
        from contextlib import ExitStack
        with ExitStack() as stack:
            if hasattr(shop,'sanitize_body'): stack.enter_context(patch.object(shop,'sanitize_body',side_effect=lambda env,body:body))
            stack.enter_context(patch.object(shop,'_http',return_value=(503,b'')))
            with self.assertRaises(gate.ReviewRequired): safe.prepare_final(self.env,a,a['body_html'],a['featured_image_url'])

    def test_step_publisher_rejects_stale_image_state_before_article_write(self):
        import publish_step
        a=fixture(); write=Path(self.tmp.name)
        (write/'2099-10-05-article.json').write_text(json.dumps(a))
        state={str(i):{'url':'https://cdn.example/x.webp','alt':'x','article_sha256':'stale','vision_verified':True} for i in range(3)}
        (write/'2099-10-05-img-urls.json').write_text(json.dumps(state))
        with patch.object(publish_step,'OUTPUT_DIR',write), patch.object(publish_step,'load_env',return_value=self.env), patch.object(publish_step,'load_yaml',side_effect=[{'2099-10-05':self.entry},self.cols]), patch.object(publish_step,'review_facts'), patch.object(publish_step,'create_article') as create, patch.object(sys,'argv',['publish_step.py','--date','2099-10-05','--step','article']):
            with self.assertRaises(RuntimeError): publish_step.main()
            create.assert_not_called()

    def test_manual_days_without_start_are_used(self):
        with patch.object(weekly,'ensure_dirs'), patch.object(weekly,'load_env',return_value=self.env), patch.object(weekly,'load_yaml',side_effect=[{},{}]), patch.object(weekly,'process_one_day',return_value={'status':'already_exists'}) as day, patch.object(sys,'argv',['weekly.py','--days','2']):
            self.assertEqual(weekly.main(),0); self.assertEqual(day.call_count,2)

    def test_weekly_review_required_sets_failure_exit(self):
        with patch.object(weekly,'ensure_dirs'), patch.object(weekly,'load_env',return_value=self.env), patch.object(weekly,'load_yaml',side_effect=[{},{}]), patch.object(weekly,'process_one_day',return_value={'status':'review_required'}), patch.object(sys,'argv',['weekly.py','--days','1']):
            self.assertEqual(weekly.main(),1)

    def test_weekly_normal_article_reaches_confirmed_schedule_automatically(self):
        a=fixture(); posted={}
        def api(env,path,**kw):
            if path=='shop.json': return {'shop':{'name':safe.BRAND,'domain':safe.DOMAIN.removeprefix('https://')}}
            posted.update(kw['body']['article'])
            return {'article':{'id':55,'handle':a['url_slug']}}
        def gql(env,q,v):
            if 'query Blogs' in q: return {'blogs':{'nodes':[{'id':'gid://shopify/Blog/1','handle':safe.BLOG_HANDLE}],'pageInfo':{'hasNextPage':False,'endCursor':None}}}
            if 'query BlogArticles' in q: return {'blog':{'id':'gid://shopify/Blog/1','handle':safe.BLOG_HANDLE,'articles':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}}}
            if 'mutation' in q: return {'articleUpdate':{'article':{'id':'gid://shopify/Article/55'},'userErrors':[]}}
            return {'article':{'id':'gid://shopify/Article/55','body':posted['body_html'],'publishedAt':'2099-10-05T07:00:00Z','isPublished':False}}
        env={**self.env,'ANTHROPIC_API_KEY':'mock','ANTHROPIC_MODEL':'mock','GOOGLE_API_KEY':'mock'}
        import content
        with patch.object(shop,'_url_alive',return_value=True,create=True), patch.object(weekly,'generate_full_article',return_value=a), patch.object(content,'_claude_call',return_value=json.dumps(clear_review(None))), patch.object(fact_review,'load_sources',return_value=[]), patch.object(weekly,'generate_image_for_slot',return_value={'webp_bytes':b'webp','filename':'photo.webp','vision_verified':True}), patch.object(weekly,'upload_image',side_effect=['https://cdn.example/hero.webp','https://cdn.example/1.webp','https://cdn.example/2.webp']), patch.object(shop,'_api',side_effect=api), patch.object(shop,'_gql',side_effect=gql), patch.object(shop,'_http',return_value=(200,b'')):
            result=weekly.process_one_day('2099-10-05',env,{'2099-10-05':self.entry},self.cols)
            self.assertEqual(result['status'],'success',result)
            self.assertEqual(result['scheduled_at'],'2099-10-05T07:00:00Z')
            self.assertEqual(result['score'],8)
            self.assertTrue(list(Path(self.tmp.name).glob('*-final.json')))

    def test_care_cta_never_injects_unrelated_bestseller(self):
        if not (ROOT/'src/product_cta.py').exists(): self.skipTest('No product_cta helper in this repository')
        import product_cta
        a=fixture()
        def unexpected(*args): self.fail('A generic care CTA must not trigger catalog fallback')
        body,handle,ok=product_cta.ensure_product_cta(unexpected,self.entry,self.cols,a['body_html'],safe.DOMAIN)
        self.assertTrue(ok); self.assertIsNone(handle); self.assertEqual(body,a['body_html'])

    def test_upload_records_must_match_final_image_urls(self):
        a=fixture(True); a['uploaded_images'][1]['url']='https://cdn.example/wrong.webp'
        self.assertFalse(validators.validate(a,final=True)['ok'])

    def test_receipt_blocks_ambiguous_post_retry(self):
        a=fixture(True)
        with patch.object(safe,'prepare_final',return_value=a), patch.object(safe,'_articles',return_value=iter([])), patch.object(shop,'_api',side_effect=TimeoutError('ambiguous create')) as post:
            with self.assertRaises(TimeoutError): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'])
            with self.assertRaises(gate.ReviewRequired): safe.create_article(self.env,blog_id=1,article=a,featured_image_url=a['featured_image_url'],featured_image_alt='Hero',body_html=a['body_html'])
            self.assertEqual(post.call_count,1)


if __name__ == '__main__': unittest.main()
