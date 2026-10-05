"""Independent regressions for requested safety-review coverage examples."""
import test_regressions as base
import unittest


class CoverageExamples(unittest.TestCase):
    def test_empty_review_cannot_clear_requested_coverage_examples(self):
        statements = (
            'Leave the warmer on overnight.',
            'Mix the essential oil with a carrier oil.',
            'This blend contains no allergens.',
            'Avoid this material if you have an allergy.',
            'Dissolve the powder completely before use.',
            'The granules dissolve in water.',
        )
        for statement in statements:
            with self.subTest(statement=statement):
                article = base.fixture()
                article['body_html'] = '<p>' + statement + '</p>'
                with self.assertRaises(base.gate.ReviewRequired):
                    base.fact_review.review_facts(article, {}, reviewer=base.clear_review, sources=[])
                self.assertIn('coverage_incomplete', article['fact_review']['issues'][0]['reason'])

    def test_supported_coverage_examples_can_pass_contextual_review(self):
        statement = 'Do not leave the warmer on overnight.'
        article = base.fixture()
        article['body_html'] = '<p>' + statement + '</p>'
        source = {'id': 'manufacturer', 'kind': 'manufacturer', 'text': statement}
        review = {**base.clear_review(None), 'claims': [{
            'quote': statement, 'category': 'safety', 'source_id': 'manufacturer',
            'source_excerpt': statement, 'support_explanation': 'Exact manufacturer warning.'}]}
        result = base.fact_review.review_facts(article, {}, reviewer=lambda _: review, sources=[source])
        self.assertEqual(result['status'], 'passed')

    def test_ordinary_advice_remains_automatic(self):
        article = base.fixture()
        self.assertEqual(base.fact_review.review_facts(
            article, {}, reviewer=base.clear_review, sources=[])['status'], 'passed')


if __name__ == '__main__': unittest.main()
