"""Which model does which job, locked here (owner, 2026-10-13, from his OpenRouter model guide: Claude Sonnet 5.5 is
the best overall at $2/$10, Opus 5.5 the best coder at $4/$20, Haiku 5.5 the cheap one at $0.10/$0.50, and the backup
is from a DIFFERENT maker so that an Anthropic outage does not take the backup down too). All go through OpenRouter
on OPENROUTER_API_KEY; the key's guardrail was opened to them the same day. Qwen (the morning's choice) is not used.

A .env line (OPENROUTER_MODEL, OPENROUTER_CODING_MODEL, OPENROUTER_RESEARCH_MODEL, OPENROUTER_HELPER_MODEL) may swap a
job to another model FROM THIS LIST only; a name that is not on the list is ignored, so these stay the only models she
uses. asi1-mini (ASI:One, 2026-10-14) is on the list as an extra: picked by such a line, it is called at api.asi1.ai
with ASI_ONE_API_KEY instead of OpenRouter.
"""
import os

OPENROUTER_BASE = 'https://openrouter.ai/api/v1'
ASI_BASE = 'https://api.asi1.ai/v1'

# Where a model is served, and the .env key for it. ASI:One (Fetch.ai, owner 2026-10-14: "Add ASI:One (asi1-mini) to
# VERONICA's model list as an extra model") is not on OpenRouter: it has its own OpenAI-style endpoint and key.
PROVIDERS = {'openrouter': (OPENROUTER_BASE, 'OPENROUTER_API_KEY'), 'asi': (ASI_BASE, 'ASI_ONE_API_KEY')}
SERVED_BY = {'asi1-mini': 'asi'}      # everything not listed here goes through OpenRouter

MODELS = {
    'chat': 'anthropic/claude-sonnet-5.5',        # VERONICA herself: talking, tools, email, calendar
    'coding': 'anthropic/claude-opus-5.5',        # the coder subagent
    'research': 'anthropic/claude-sonnet-5.5',    # background research tasks (research.py)
    'helper': 'xiaomi/mimo-v2.6-flash',           # the researcher and secretary subagents (owner, 2026-10-14:
                                                  # MiMo-V2.6-Flash instead of Haiku 5.5; $0.14/$0.28, tools ok)
    'backup': 'google/gemini-3.1-flash-lite',     # when Anthropic fails twice in a row
}

ALLOWED = frozenset(MODELS.values()) | {'google/gemini-3.1-pro-preview', 'anthropic/claude-haiku-5.5', 'asi1-mini'}

LABELS = {
    'anthropic/claude-sonnet-5.5': 'Claude Sonnet 5.5',
    'anthropic/claude-opus-5.5': 'Claude Opus 5.5',
    'anthropic/claude-haiku-5.5': 'Claude Haiku 5.5',
    'xiaomi/mimo-v2.6-flash': 'MiMo-V2.6-Flash',
    'google/gemini-3.1-flash-lite': 'Gemini 3.1 Flash-Lite',
    'google/gemini-3.1-pro-preview': 'Gemini 3.1 Pro',
    'asi1-mini': 'ASI:One Mini',
}

ENV = {'chat': 'OPENROUTER_MODEL', 'coding': 'OPENROUTER_CODING_MODEL', 'research': 'OPENROUTER_RESEARCH_MODEL',
       'helper': 'OPENROUTER_HELPER_MODEL'}


def for_job(job):
    """The model for a job, read each time it is asked for (app.py reads .env after importing agent.py)."""
    wanted = os.environ.get(ENV.get(job, ''), '').strip()
    if wanted in ALLOWED:
        return wanted
    return MODELS[job]


def chat_model(): return for_job('chat')
def coding_model(): return for_job('coding')
def research_model(): return for_job('research')
def helper_model(): return for_job('helper')
def backup_model(): return for_job('backup')


def label(model_id):
    return LABELS.get(model_id, model_id)


def provider(model_id):
    """'openrouter' or 'asi': who serves this model."""
    return SERVED_BY.get(model_id, 'openrouter')


def endpoint(model_id):
    """(base URL, name of the .env key) for this model."""
    return PROVIDERS[provider(model_id)]


def extra_body(model_id, thinking):
    """OpenRouter's reasoning settings go only to OpenRouter; ASI:One's mini model has no reasoning budget."""
    return thinking if provider(model_id) == 'openrouter' else None
