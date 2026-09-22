"""Behavioral tests: real selection logic, independent numerical expectations."""
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def config():
    return {
        'version': 1,
        'jev': {'endpoint': 'https://api.typesafe.ai/v1/systemone',
                'model': 'jev-latest', 'api_key_env': 'TYPESAFE_API_KEY'},
        'scenarios': {
            'complexity': {'type': 'choice', 'instructions': 'Assess reasoning complexity.',
                           'criteria': {'simple': 'One step', 'medium': 'Several steps', 'complex': 'Interdependent steps'}},
            'coding': {'type': 'noul', 'instructions': 'Requires code changes?'},
            'depth': {'type': 'score', 'instructions': 'Rate reasoning depth.',
                      'criteria': ['Direct', 'Some reasoning', 'Extended reasoning']},
        },
        'models': {m: {'model': m} for m in ['fast', 'coder', 'strong']},
        'routing': {
            'mode': 'rules', 'match': 'first', 'fallback': 'strong',
            'on_jev_error': 'fallback',
            'rules': [
                {'id': 'complex', 'all': [{'field': 'complexity.probabilities.complex', 'op': 'gte', 'value': .65}], 'target': 'strong'},
                {'id': 'code', 'all': [{'field': 'coding.noul', 'op': 'gte', 'value': .8}], 'target': 'coder'},
                {'id': 'simple', 'all': [{'field': 'complexity.probabilities.simple', 'op': 'gte', 'value': .85},
                                        {'field': 'coding.noul', 'op': 'lte', 'value': .2}], 'target': 'fast'},
            ],
        },
    }


def response(simple=.1, medium=.2, complex_=.7, coding=.9):
    probs = {'simple': simple, 'medium': medium, 'complex': complex_}
    return {'model': 'jev-fixture', 'answers': {
        'complexity': {'type': 'choice', 'choice': max(probs, key=probs.get), 'probabilities': probs, 'confidence': .35},
        'coding': {'type': 'noul', 'noul': coding},
        'depth': {'type': 'score', 'score': 1.5, 'confidence': .4,
                  'probabilities': {'0': 0., '1': .5, '2': .5},
                  'legend': {'0': 'Direct', '1': 'Some reasoning', '2': 'Extended reasoning'}},
    }, 'usage': {'input_tokens': 12, 'output_tokens': 8}}


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Allows a clean baseline failure before any production file exists.
        cls.available = importlib.util.find_spec('engine') is not None
        if cls.available:
            import engine
            import configuration
            cls.engine, cls.configuration = engine, configuration

    def setUp(self):
        self.assertTrue(self.available, 'Routing implementation does not exist yet')

    def decide(self, cfg=None, res=None, **kwargs):
        return self.engine.decide(cfg or config(), res or response(), **kwargs)

    def test_first_match_uses_probability_not_confidence(self):
        result = self.decide()
        self.assertEqual(result['selected'], 'strong')
        self.assertEqual(result['rule'], 'complex')
        self.assertEqual(result['signals']['complexity.confidence'], .35)

    def test_threshold_equality_and_fallback(self):
        for res, expected, reason in [
            (response(.15, .2, .65), 'strong', 'rule_match'),
            (response(.2, .4, .4, .8), 'coder', 'rule_match'),
            (response(.85, .1, .05, .2), 'fast', 'rule_match'),
            (response(.4, .3, .3, .5), 'strong', 'no_match'),
        ]:
            with self.subTest(expected=expected, reason=reason):
                result = self.decide(res=res)
                self.assertEqual((result['selected'], result['reason']), (expected, reason))

    def test_nested_any_all_and_choice_equality(self):
        cfg = config()
        cfg['routing']['rules'] = [{'id': 'nested', 'all': [
            {'field': 'coding.noul', 'op': 'gt', 'value': .5},
            {'any': [{'field': 'complexity.choice', 'op': 'eq', 'value': 'complex'},
                     {'field': 'depth.normalized', 'op': 'gte', 'value': .9}]}], 'target': 'coder'}]
        self.assertEqual(self.decide(cfg)['selected'], 'coder')

    def test_confidence_guard_falls_back_without_inventing_noul_confidence(self):
        cfg = config()
        cfg['routing']['require'] = {'field': 'complexity.confidence', 'op': 'gte', 'value': .5}
        self.assertEqual(self.decide(cfg)['reason'], 'guard_failed')
        cfg['routing']['require']['field'] = 'coding.confidence'
        with self.assertRaises(self.configuration.ConfigError):
            self.decide(cfg)

    def test_weighted_score_normalization_and_tie_order(self):
        cfg = config()
        cfg['routing'].update(mode='weighted', weighted={
            'candidates': {
                'strong': {'terms': [{'field': 'complexity.probabilities.complex', 'weight': .7},
                                     {'field': 'coding.noul', 'weight': .3}]},
                'coder': {'terms': [{'field': 'depth.normalized', 'weight': 1.0}]},
            }, 'tie_break': ['coder', 'strong'], 'min_margin': 0})
        result = self.decide(cfg)
        self.assertEqual(result['selected'], 'strong')
        self.assertAlmostEqual(result['scores']['strong'], .76)
        self.assertAlmostEqual(result['scores']['coder'], .75)
        cfg['routing']['weighted']['candidates']['strong'] = {'bias': .75, 'terms': []}
        self.assertEqual(self.decide(cfg)['selected'], 'coder')
        cfg['routing']['weighted']['min_margin'] = .01
        self.assertEqual(self.decide(cfg)['reason'], 'small_margin')

    def test_weighted_eligibility_and_negative_coefficients(self):
        cfg = config()
        cfg['routing'].update(mode='weighted', weighted={'candidates': {
            'fast': {'bias': 1, 'terms': [{'field': 'coding.noul', 'weight': -1}]},
            'coder': {'when': {'field': 'coding.noul', 'op': 'lt', 'value': .2}, 'bias': 10, 'terms': []},
        }, 'tie_break': ['fast', 'coder']})
        self.assertEqual(self.decide(cfg)['selected'], 'fast')

    def test_traffic_weights_use_repeatable_buckets(self):
        cfg = config()
        cfg['routing']['rules'][0].pop('target')
        cfg['routing']['rules'][0]['traffic'] = {'fast': 80, 'strong': 20, 'coder': 0}
        results = [self.decide(cfg, request_id=f'req-{i}')['selected'] for i in range(1000)]
        self.assertEqual(results, [self.decide(cfg, request_id=f'req-{i}')['selected'] for i in range(1000)])
        self.assertNotIn('coder', results)
        self.assertGreater(results.count('fast'), 730)
        self.assertLess(results.count('fast'), 870)

    def test_invalid_answer_types_ranges_and_distributions_are_rejected(self):
        changes = [
            ('coding', {'type': 'noul', 'noul': float('nan')}),
            ('coding', {'type': 'noul', 'noul': True}),
            ('coding', {'type': 'noul', 'noul': 1.1}),
            ('coding', {'type': 'choice', 'noul': .8}),
            ('complexity', {'type': 'choice', 'choice': 'complex', 'confidence': .5,
                            'probabilities': {'simple': .2, 'medium': .2, 'complex': .2}}),
        ]
        for key, value in changes:
            with self.subTest(value=value):
                res = response()
                res['answers'][key] = value
                with self.assertRaises(self.engine.ResponseError):
                    self.decide(res=res)
        res = response()
        del res['answers']['coding']
        with self.assertRaises(self.engine.ResponseError):
            self.decide(res=res)
        res = response()
        res['answers']['depth']['score'] = .2
        with self.assertRaises(self.engine.ResponseError):
            self.decide(res=res)

    def test_invalid_config_fails_before_routing(self):
        cases = []
        for key, value in [('target', 'missing'), ('traffic', {'fast': -1})]:
            cfg = config()
            cfg['routing']['rules'][0].pop('target')
            cfg['routing']['rules'][0][key] = value
            cases.append(cfg)
        cfg = config()
        cfg['routing']['rules'][0]['all'][0]['field'] = 'complexity.probabilities.typo'
        cases.append(cfg)
        cfg = config()
        cfg['routing']['rules'][0]['all'][0]['op'] = 'eval'
        cases.append(cfg)
        cfg = config()
        cfg['routing']['fallback'] = 'absent'
        cases.append(cfg)
        cfg = config()
        cfg['routing']['rulse'] = []
        cases.append(cfg)
        for cfg in cases:
            with self.subTest(cfg=cfg):
                with self.assertRaises(self.configuration.ConfigError):
                    self.decide(cfg)


if __name__ == '__main__':
    unittest.main()
