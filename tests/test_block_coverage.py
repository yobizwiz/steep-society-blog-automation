"""Offline regressions for structural factual checkpoints and response telemetry."""
import test_regressions as base
import io
import json
import unittest
from unittest.mock import patch


def review_for(*quotes):
    return {**base.clear_review(None),'claims':[{'quote':q,'category':'safety','source_id':'fixture',
        'source_excerpt':q,'support_explanation':'Synthetic exact fixture, not a real safety source.'} for q in quotes]}


class BlockCoverageRegressions(unittest.TestCase):
    def run_review(self, html, *quotes):
        article=base.fixture();article['body_html']=html
        result=base.fact_review.review_facts(article,{},reviewer=lambda _:review_for(*quotes),
            sources=[{'id':'fixture','kind':'manufacturer','text':'\n'.join(quotes)}])
        return article,result

    def checkpoints(self, html):
        article=base.fixture();article['body_html']=html
        payload=base.fact_review.factual_payload(article)
        return payload,base.fact_review.risk_sentences(payload)

    def test_claude_list_reproduction_accepts_complete_item_quote(self):
        html='<h2>Safety</h2><ul><li>Keep diffusers away from children</li><li>Use 3 drops per 100 ml of water</li></ul><p>Enjoy.</p>'
        quote='Keep diffusers away from children'
        # This tests ledger coverage only; the injected reviewer cannot certify the other claims.
        _,result=self.run_review(html,quote)
        self.assertEqual(result['risk_sentence_coverage'],{'total':1,'covered':1,'missing':[]})
        self.assertEqual(self.checkpoints(html)[1],[quote])

    def test_second_list_item_cannot_be_cleared_by_first(self):
        one='Never microwave a sealed jar';two='Keep diffusers away from children'
        with self.assertRaises(base.gate.ReviewRequired):
            self.run_review('<ul><li>'+one+'</li><li>'+two+'</li></ul>',one)

    def test_table_cells_are_separate_complete_checkpoints(self):
        quote='Do not microwave sealed jars'
        payload,checkpoints=self.checkpoints('<table><tr><th>Care</th><th>Choice</th></tr><tr><td>'+quote+'</td><td>Prefer a calm look</td></tr></table>')
        self.assertEqual(payload['blocks'],['Care','Choice',quote,'Prefer a calm look'])
        self.assertEqual(checkpoints,[quote])
        self.run_review('<table><tr><td>'+quote+'</td><td>Choose a look.</td></tr></table>',quote)

    def test_nested_blocks_are_not_duplicated(self):
        quote='Keep diffusers away from children'
        payload,checkpoints=self.checkpoints('<div><blockquote><p>'+quote+'</p></blockquote><ul><li><p>Enjoy.</p><ul><li>Choose a look.</li></ul></li></ul></div>')
        self.assertEqual(payload['blocks'],[quote,'Enjoy.','Choose a look.'])
        self.assertEqual(checkpoints,[quote])

    def test_inline_and_line_break_preserve_negation(self):
        html='<p>Never<br>microwave <em>sealed</em> jars.</p>'
        full='Never microwave sealed jars.'
        self.assertEqual(self.checkpoints(html)[1],[full])
        self.run_review(html,full)
        with self.assertRaises(base.gate.ReviewRequired):self.run_review(html,'microwave sealed jars.')

    def test_risk_claims_in_headings_and_headers_remain_checked(self):
        for tag in ('h2','h3','h4','th'):
            with self.subTest(tag=tag),self.assertRaises(base.gate.ReviewRequired):
                self.run_review('<'+tag+'>Microwave sealed jars</'+tag+'><p>A calm arrangement.</p>','A calm arrangement.')

    def test_root_text_and_captions_are_not_dropped(self):
        first='Do not microwave sealed jars';second='Keep diffusers away from children'
        html=first+'<figure><figcaption>'+second+'</figcaption></figure>'
        self.assertEqual(self.checkpoints(html)[1],[first,second])


class FactResponseMetrics(unittest.TestCase):
    def call_api(self, article, response):
        env={'ANTHROPIC_API_KEY':'offline-fixture','ANTHROPIC_MODEL':'fixture-request-model'}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
            return base.fact_review.review_facts(article,env,sources=[])

    def response(self, stop='end_turn', text=None):
        return {'model':'fixture-returned-model','stop_reason':stop,
            'usage':{'input_tokens':321,'output_tokens':42,'cache_read_input_tokens':20},
            'content':[{'type':'text','text':text if text is not None else json.dumps({**base.clear_review(None),'metrics':{'checkpoint_count':999}})}]}

    def test_success_records_provider_usage_and_overrides_model_metrics(self):
        article=base.fixture();result=self.call_api(article,self.response())
        metrics=result['metrics']
        self.assertEqual(metrics['model'],'fixture-returned-model')
        self.assertEqual(metrics['input_tokens'],321)
        self.assertEqual(metrics['output_tokens'],42)
        self.assertEqual(metrics['cache_read_input_tokens'],20)
        self.assertEqual(metrics['checkpoint_count'],0)
        self.assertEqual(metrics['max_output_tokens'],6000)
        self.assertEqual(metrics['stop_reason'],'end_turn')
        self.assertIsNone(metrics['cache_creation_input_tokens'])
        self.assertEqual(metrics,article['fact_review_metrics'])

    def test_truncation_holds_and_retains_response_metrics(self):
        article=base.fixture()
        with self.assertRaises(base.gate.ReviewRequired):self.call_api(article,self.response(stop='max_tokens'))
        self.assertEqual(article['fact_review_metrics']['stop_reason'],'max_tokens')
        self.assertEqual(article['fact_review_metrics']['output_tokens'],42)
        self.assertTrue(article['fact_review_metrics']['response_received'])

    def test_strict_json_failure_also_retains_metrics(self):
        article=base.fixture()
        with self.assertRaises(base.gate.ReviewRequired):self.call_api(article,self.response(text='{"unfinished":'))
        self.assertEqual(article['fact_review_metrics']['stop_reason'],'end_turn')
        self.assertEqual(article['fact_review_metrics']['input_tokens'],321)

    def test_missing_usage_and_injected_review_are_not_zero_cost(self):
        article=base.fixture();response=self.response();response.pop('usage')
        result=self.call_api(article,response)
        self.assertIsNone(result['metrics']['output_tokens'])
        result=base.fact_review.review_facts(article,{},reviewer=base.clear_review,sources=[])
        self.assertEqual(result['metrics']['execution'],'injected_reviewer')
        self.assertFalse(result['metrics']['response_received'])
        self.assertIsNone(result['metrics']['stop_reason'])
        self.assertIsNone(result['metrics']['output_tokens'])


if __name__=='__main__':unittest.main()
