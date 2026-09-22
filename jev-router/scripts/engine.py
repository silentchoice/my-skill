"""Pure routing; performs no network calls and does not mutate configuration."""
import hashlib
import math
import operator
import secrets

from configuration import condition_of, number, validate_config


class ResponseError(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise ResponseError(message)


def probability(value):
    return number(value) and 0 <= value <= 1


def validate_answers(config, response):
    check(isinstance(response, dict) and isinstance(response.get('answers'), dict), 'Response is missing answers')
    signals = {}
    for key, question in config['scenarios'].items():
        answer = response['answers'].get(key)
        kind = question['type']
        check(isinstance(answer, dict) and answer.get('type') == kind, f'Missing or wrong answer type: {key}')
        if kind == 'noul':
            check(probability(answer.get('noul')), f'Invalid Noul probability: {key}')
            signals[f'{key}.noul'] = answer['noul']
            continue
        check(probability(answer.get('confidence')), f'Invalid confidence: {key}')
        signals[f'{key}.confidence'] = answer['confidence']
        probs = answer.get('probabilities')
        expected = set(question['criteria']) if kind == 'choice' else set(map(str, range(len(question['criteria']))))
        check(isinstance(probs, dict) and set(probs) == expected, f'Probability options mismatch: {key}')
        check(all(probability(v) for v in probs.values()), f'Invalid probability value: {key}')
        check(math.isclose(sum(probs.values()), 1, rel_tol=0, abs_tol=1e-5), f'Probabilities do not sum to one: {key}')
        for option, value in probs.items():
            signals[f'{key}.probabilities.{option}'] = value
        if kind == 'choice':
            choice = answer.get('choice')
            check(isinstance(choice, str) and choice in expected, f'Unknown selected option: {key}')
            check(probs[choice] >= max(probs.values()) - 1e-6, f'Choice disagrees with probabilities: {key}')
            signals[f'{key}.choice'] = choice
        else:
            score = answer.get('score')
            maximum = len(question['criteria']) - 1
            check(number(score) and 0 <= score <= maximum, f'Invalid Score: {key}')
            mean = sum(int(level) * value for level, value in probs.items())
            check(math.isclose(score, mean, rel_tol=0, abs_tol=1e-4), f'Score disagrees with distribution: {key}')
            check(isinstance(answer.get('legend'), dict) and set(answer['legend']) == expected, f'Invalid Score legend: {key}')
            signals[f'{key}.score'] = score
            signals[f'{key}.normalized'] = score / maximum
    return signals


OPERATORS = {'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt,
             'gte': operator.ge, 'lt': operator.lt, 'lte': operator.le}


def evaluate(condition, signals):
    for group, combine in (('all', all), ('any', any)):
        if group in condition:
            traces = [evaluate(child, signals) for child in condition[group]]
            return {'kind': group, 'matched': combine(t['matched'] for t in traces), 'children': traces}
    value = signals[condition['field']]
    return {**condition, 'actual': value, 'matched': OPERATORS[condition['op']](value, condition['value'])}


def fallback(config, reason, **details):
    return {'selected': config['routing']['fallback'], 'reason': reason, 'rule': None,
            'signals': {}, 'scores': {}, 'trace': [], **details}


def allocate(traffic, rule_id, request_id):
    if request_id is None:
        draw = secrets.randbits(53) / 2**53
        method = 'random'
    else:
        digest = hashlib.sha256((rule_id + '\0' + request_id).encode('utf-8')).digest()
        draw = (int.from_bytes(digest[:8], 'big') >> 11) / 2**53
        method = 'request_hash'
    total = sum(traffic.values())
    boundary = 0
    selected = None
    for alias, weight in traffic.items():
        if weight <= 0:
            continue
        selected = alias
        boundary += weight / total
        if draw < boundary:
            break
    return selected, {'method': method, 'bucket': draw, 'weights': dict(traffic)}


def decide(config, response, request_id=None):
    validate_config(config)
    signals = validate_answers(config, response)
    routing = config['routing']
    result = fallback(config, 'no_match', signals=signals)
    if 'require' in routing:
        guard = evaluate(routing['require'], signals)
        result['trace'].append({'guard': guard})
        if not guard['matched']:
            result['reason'] = 'guard_failed'
            return result
    if routing['mode'] == 'rules':
        for rule in routing.get('rules', []):
            trace = evaluate(condition_of(rule), signals)
            result['trace'].append({'rule': rule['id'], **trace})
            if not trace['matched']:
                continue
            if 'target' in rule:
                selected = rule['target']
            else:
                selected, allocation = allocate(rule['traffic'], rule['id'], request_id)
                result['allocation'] = allocation
            result.update(selected=selected, reason='rule_match', rule=rule['id'])
            return result
        return result
    weighted = routing['weighted']
    for alias, candidate in weighted['candidates'].items():
        if 'when' in candidate:
            trace = evaluate(candidate['when'], signals)
            result['trace'].append({'candidate': alias, 'eligibility': trace})
            if not trace['matched']:
                continue
        contributions = [{'field': term['field'], 'weight': term['weight'],
                          'value': signals[term['field']],
                          'contribution': signals[term['field']] * term['weight']}
                         for term in candidate['terms']]
        score = candidate.get('bias', 0) + sum(term['contribution'] for term in contributions)
        check(number(score), 'Weighted score overflow; reduce coefficients')
        result['scores'][alias] = score
        result['trace'].append({'candidate': alias, 'bias': candidate.get('bias', 0), 'terms': contributions, 'score': score})
    ranking = sorted(result['scores'], key=lambda a: (-result['scores'][a], weighted['tie_break'].index(a)))
    if not ranking:
        result['reason'] = 'no_eligible_model'
        return result
    if len(ranking) > 1:
        margin = result['scores'][ranking[0]] - result['scores'][ranking[1]]
        result['margin'] = margin
        if margin < weighted.get('min_margin', 0):
            result['reason'] = 'small_margin'
            return result
    result.update(selected=ranking[0], reason='weighted_score')
    return result
