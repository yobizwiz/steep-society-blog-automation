"""Offline regressions for Claude's second review; import network guard first."""
import test_regressions as base
import json
import unittest


WARNING = 'Never leave a candle warmer unattended near children.'


def supported_review(quote):
    source = {'id':'source','kind':'manufacturer','text':quote}
    review = {**base.clear_review(None), 'claims':[{'quote':quote,'category':'safety',
        'source_id':'source','source_excerpt':quote,'support_explanation':'Fixture supports this exact statement.'}]}
    return review, [source]


class RiskSentenceCoverage(unittest.TestCase):
    def review(self, text, result, sources):
        article = base.fixture();article['body_html'] = '<p>'+text+'</p>'
        return article, lambda: base.fact_review.review_facts(article, {}, reviewer=lambda _:result, sources=sources)

    def test_supported_unrelated_claim_does_not_clear_omitted_risk(self):
        review,sources = supported_review('A calm arrangement.')
        article,run = self.review('A calm arrangement. '+WARNING, review, sources)
        with self.assertRaises(base.gate.ReviewRequired):run()
        self.assertEqual(article['fact_review']['risk_sentence_coverage']['missing'], [WARNING])

    def test_single_risk_word_quote_does_not_cover_whole_sentence(self):
        review,sources = supported_review('children')
        article,run = self.review(WARNING,review,sources)
        with self.assertRaises(base.gate.ReviewRequired):run()
        self.assertEqual(article['fact_review']['risk_sentence_coverage']['covered'], 0)

    def test_quote_omitting_negation_does_not_discharge_warning(self):
        review,sources = supported_review('leave a candle warmer unattended near children')
        _,run = self.review(WARNING,review,sources)
        with self.assertRaises(base.gate.ReviewRequired):run()

    def test_complete_supported_warning_passes(self):
        review,sources = supported_review(WARNING)
        _,run = self.review(WARNING,review,sources)
        result = run()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['risk_sentence_coverage'], {'total':1,'covered':1,'missing':[]})

    def test_second_unreviewed_risk_sentence_still_blocks(self):
        second = 'Do not microwave a sealed jar.'
        review,sources = supported_review(WARNING)
        article,run = self.review(WARNING+' '+second,review,sources)
        with self.assertRaises(base.gate.ReviewRequired):run()
        self.assertEqual(article['fact_review']['risk_sentence_coverage']['missing'], [second])

    def test_decimal_and_trailing_punctuation_preserve_full_coverage(self):
        text = 'The specified test temperature is 48.5°F.'
        review,sources = supported_review(text[:-1])
        _,run = self.review(text,review,sources)
        self.assertEqual(run()['risk_sentence_coverage']['total'], 1)

    def test_model_supplied_coverage_counts_are_overwritten(self):
        review,sources = supported_review('A calm arrangement.')
        review['risk_sentence_coverage'] = {'total':1,'covered':1,'missing':[]}
        article,run = self.review('A calm arrangement. '+WARNING,review,sources)
        with self.assertRaises(base.gate.ReviewRequired):run()
        self.assertEqual(article['fact_review']['risk_sentence_coverage']['covered'], 0)

    def test_reviewer_receives_exact_risk_sentence_checkpoints(self):
        captured=[];article=base.fixture();article['body_html']='<p>'+WARNING+'</p>'
        def reviewer(prompt):captured.append(json.loads(prompt));return base.clear_review(None)
        with self.assertRaises(base.gate.ReviewRequired):base.fact_review.review_facts(article,{},reviewer=reviewer,sources=[])
        self.assertEqual(captured[0]['risk_sentences_requiring_assessment'], [WARNING])

    def test_conventional_button_class_outside_cta_is_second_cta(self):
        for classname in ('cta-button','btn-primary','button--primary','cta__button'):
            with self.subTest(classname=classname):
                article=base.fixture()
                article['body_html']='<a class="'+classname+'" href="'+base.safe.DOMAIN+'/products/random">Buy</a>'+article['body_html']
                rules=[v['rule'] for v in base.validators.validate(article)['violations']]
                self.assertIn('cta_count',rules)

    def test_incidental_button_substring_does_not_make_reference_a_cta(self):
        article=base.fixture()
        article['body_html']='<p><a class="buttonless-reference" href="'+base.safe.DOMAIN+'/products/reference">Reference</a></p>'+article['body_html']
        self.assertTrue(base.validators.validate(article)['ok'])


if __name__=='__main__':unittest.main()
