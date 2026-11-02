"""CLI defaults and explicit, task-scoped model requests."""
import re

CLI_TYPES = {'codex_cli', 'claude_code', 'gemini_cli'}


def default_settings():
    return {'use_cli_defaults': True, 'effort_selection': '', 'reasoning_effort': '',
            'discovered_model': 'default',
            'capabilities': {'models':['default'], 'efforts':['medium'], 'effort_map':{'medium':''}}}


def explicit_model_request(prompt):
    # Only an instruction at the start of the current turn changes routing.
    # Model mentions in quoted material, ordinary prose, and negations do not.
    match = re.match(
        r'^\s*(?:please\s+)?(?:(?:can|could)\s+you\s+)?'
        r'(?:(?:for\s+this\s+(?:task|run|reply)),?\s+)?'
        r'(?:use\s+|switch\s+to\s+|change\s+(?:the\s+)?model\s+to\s+|/model\s+)'
        r'(?:(?:claude|codex|openai|gemini)\s+)?(?:model\s+)?'
        r'["`\']?(?P<model>gpt-[a-z0-9][a-z0-9._-]*|o[134](?:-[a-z0-9._-]+)?|'
        r'claude-[a-z0-9][a-z0-9._-]*|gemini-[a-z0-9][a-z0-9._-]*|opus|sonnet|haiku|fable)'
        r'(?=$|[\s"`\',;:!?])', prompt, re.IGNORECASE)
    if not match:
        return None
    model = match['model'].lower().rstrip('.')
    if model in ('opus', 'sonnet', 'haiku', 'fable'):
        version = re.match(r'\s+(\d+\.\d+)(?=$|[\s,;:!?])', prompt[match.end():])
        if version:
            model = 'claude-' + model + '-' + version[1].replace('.', '-')
    runtime_type = 'codex_cli' if model.startswith(('gpt-', 'o1', 'o3', 'o4')) else 'gemini_cli' if model.startswith('gemini-') else 'claude_code'
    return runtime_type, model
