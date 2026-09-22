"""Strict, non-executable configuration for Jev routing."""
import json
import math
import re
from pathlib import Path


class ConfigError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConfigError(message)


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def text(value):
    return isinstance(value, str) and bool(value.strip())


def shape(value, allowed, required=()):
    require(isinstance(value, dict), 'Expected a mapping')
    require(all(isinstance(k, str) for k in value), 'Mapping keys must be strings; quote YAML true/false')
    require(not (set(value) - set(allowed)), f'Unknown configuration keys: {set(value) - set(allowed)}')
    require(set(required) <= set(value), f'Missing configuration keys: {set(required) - set(value)}')


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]+', value), 'IDs must contain letters, digits, _ or -')


def load_config(path):
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError('PyYAML is required: run with uv run --script scripts/router.py') from exc

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        # No merge keys: duplicate/merged policy values must never silently win.
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            require(isinstance(key, str), 'YAML mapping keys must be strings; quote true/false')
            require(key not in result, f'Duplicate configuration key: {key}')
            result[key] = loader.construct_object(value_node, deep=True)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        config = yaml.load(Path(path).read_text(encoding='utf-8-sig'), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ConfigError('Invalid YAML (check syntax; aliases/merge keys are not supported)') from exc
    return validate_config(config)


def field_types(config):
    fields = {}
    for key, question in config['scenarios'].items():
        kind = question['type']
        if kind == 'noul':
            fields[f'{key}.noul'] = 'number'
        else:
            fields[f'{key}.confidence'] = 'number'
            options = question['criteria'] if kind == 'choice' else map(str, range(len(question['criteria'])))
            for option in options:
                fields[f'{key}.probabilities.{option}'] = 'number'
            if kind == 'choice':
                fields[f'{key}.choice'] = 'string'
            else:
                fields[f'{key}.score'] = 'number'
                fields[f'{key}.normalized'] = 'number'
    return fields


def condition_check(condition, fields, depth=0):
    require(depth < 20, 'Condition nesting exceeds 20 levels')
    shape(condition, {'all', 'any', 'field', 'op', 'value'})
    groups = set(condition) & {'all', 'any'}
    if groups:
        require(len(groups) == 1 and len(condition) == 1, 'Use exactly one of all/any per group')
        children = condition[next(iter(groups))]
        require(isinstance(children, list) and len(children) > 0, 'Condition groups must be nonempty lists')
        for child in children:
            condition_check(child, fields, depth + 1)
    else:
        require(set(condition) == {'field', 'op', 'value'}, 'A comparison needs field/op/value')
        field = condition['field']
        require(isinstance(field, str) and field in fields, 'Unknown signal field')
        op = condition['op']
        require(isinstance(op, str) and op in {'eq', 'ne', 'gt', 'gte', 'lt', 'lte'}, 'Unknown comparison operator')
        if fields[field] == 'number':
            require(number(condition['value']), 'Numeric comparison requires a finite number')
        else:
            require(op in {'eq', 'ne'} and isinstance(condition['value'], str), 'Choice text supports eq/ne only')


def condition_of(rule):
    return {key: rule[key] for key in ('all', 'any') if key in rule}


def validate_config(config):
    try:
        json.dumps(config, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError('Configuration must contain JSON-compatible values with finite numbers; quote dates') from exc
    shape(config, {'version', 'jev', 'scenarios', 'models', 'routing'}, {'version', 'jev', 'scenarios', 'models', 'routing'})
    require(type(config['version']) is int and config['version'] == 1, 'Only version 1 is supported')
    jev = config['jev']
    shape(jev, {'endpoint', 'model', 'api_key_env', 'timeout_seconds'}, {'endpoint', 'model', 'api_key_env'})
    for key in ('endpoint', 'model', 'api_key_env'):
        require(text(jev[key]), f'jev.{key} must be nonempty')
    require(number(jev.get('timeout_seconds', 10)) and 0 < jev.get('timeout_seconds', 10) <= 300, 'Invalid Jev timeout')
    scenarios = config['scenarios']
    require(isinstance(scenarios, dict) and scenarios, 'At least one scenario is required')
    for key, question in scenarios.items():
        identifier(key)
        shape(question, {'type', 'instructions', 'criteria'}, {'type', 'instructions'})
        kind = question['type']
        require(isinstance(kind, str) and kind in {'choice', 'score', 'noul'}, 'Unknown scenario type')
        require(isinstance(question['instructions'], (str, dict, list)) and bool(question['instructions']), 'Question instructions are required')
        criteria = question.get('criteria')
        if kind == 'choice':
            require(isinstance(criteria, dict) and 1 <= len(criteria) <= 255, 'Choice needs 1..255 options')
            for option, description in criteria.items():
                identifier(option)
                require(description is None or isinstance(description, (str, list, dict)), 'Invalid Choice description')
        elif kind == 'score':
            require(isinstance(criteria, list) and 2 <= len(criteria) <= 10, 'Score needs 2..10 ordered levels')
            require(all(isinstance(c, (str, list, dict)) and c for c in criteria), 'Score levels need descriptions')
        elif criteria is not None:
            shape(criteria, {'true', 'false'})
            require(all(isinstance(v, (str, list, dict)) for v in criteria.values()), 'Invalid Noul description')
    models = config['models']
    require(isinstance(models, dict) and models, 'At least one model is required')
    for alias, model in models.items():
        identifier(alias)
        shape(model, {'model', 'model_env', 'endpoint', 'endpoint_env', 'api_key_env', 'parameters', 'timeout_seconds'})
        require(('model' in model) != ('model_env' in model), 'Set exactly one model/model_env per target')
        require(not ('endpoint' in model and 'endpoint_env' in model), 'Set endpoint or endpoint_env, not both')
        for key in ('model', 'model_env', 'endpoint', 'endpoint_env', 'api_key_env'):
            if key in model:
                require(text(model[key]), f'Model {key} must be nonempty')
        params = model.get('parameters', {})
        require(isinstance(params, dict) and all(isinstance(k, str) for k in params), 'parameters must be a mapping')
        require(not (set(params) & {'model', 'messages', 'stream', 'n', 'tools', 'tool_choice', 'functions', 'function_call'}), 'parameters cannot override request structure or enable tool execution')
        require(number(model.get('timeout_seconds', 120)) and 0 < model.get('timeout_seconds', 120) <= 300, 'Invalid model timeout')
    routing = config['routing']
    shape(routing, {'mode', 'match', 'fallback', 'on_jev_error', 'require', 'rules', 'weighted'}, {'mode', 'fallback'})
    require(routing['mode'] in ('rules', 'weighted'), 'mode must be rules or weighted')
    require(routing.get('match', 'first') == 'first', 'Only first-match rules are supported')
    require(isinstance(routing['fallback'], str) and routing['fallback'] in models, 'Unknown fallback model')
    require(routing.get('on_jev_error', 'fallback') in ('fallback', 'stop'), 'on_jev_error must be fallback or stop')
    fields = field_types(config)
    if 'require' in routing:
        condition_check(routing['require'], fields)
    rules = routing.get('rules', [])
    require(isinstance(rules, list), 'rules must be a list')
    seen = set()
    for rule in rules:
        shape(rule, {'id', 'all', 'any', 'target', 'traffic'}, {'id'})
        identifier(rule['id'])
        require(rule['id'] not in seen, 'Duplicate rule ID')
        seen.add(rule['id'])
        condition_check(condition_of(rule), fields)
        require(('target' in rule) != ('traffic' in rule), 'Rule needs exactly one target/traffic')
        if 'target' in rule:
            require(isinstance(rule['target'], str) and rule['target'] in models, 'Unknown rule target')
        else:
            traffic = rule['traffic']
            require(isinstance(traffic, dict) and traffic, 'traffic must map model aliases to weights')
            require(all(k in models and number(w) and w >= 0 for k, w in traffic.items()), 'Invalid traffic model/weight')
            require(number(sum(traffic.values())) and sum(traffic.values()) > 0, 'Traffic total must be positive and finite')
    if routing['mode'] == 'weighted' or 'weighted' in routing:
        weighted = routing.get('weighted')
        shape(weighted, {'candidates', 'tie_break', 'min_margin'}, {'candidates', 'tie_break'})
        candidates = weighted['candidates']
        require(isinstance(candidates, dict) and candidates, 'weighted.candidates must be nonempty')
        order = weighted['tie_break']
        require(isinstance(order, list) and all(isinstance(a, str) for a in order), 'tie_break must be a list of model aliases')
        require(len(order) == len(candidates) and set(order) == set(candidates), 'tie_break must list every candidate exactly once')
        margin = weighted.get('min_margin', 0)
        require(number(margin) and margin >= 0, 'min_margin must be nonnegative')
        for alias, candidate in candidates.items():
            require(alias in models, 'Unknown weighted model')
            shape(candidate, {'bias', 'terms', 'when'}, {'terms'})
            require(number(candidate.get('bias', 0)), 'bias must be finite')
            if 'when' in candidate:
                condition_check(candidate['when'], fields)
            require(isinstance(candidate['terms'], list), 'terms must be a list')
            for term in candidate['terms']:
                shape(term, {'field', 'weight'}, {'field', 'weight'})
                require(isinstance(term['field'], str) and fields.get(term['field']) == 'number', 'Weighted terms need numeric signal fields')
                require(number(term['weight']), 'weight must be finite')
    return config
