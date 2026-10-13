"""One model per job (models.py), and the backup from another maker when the first fails (agent._ask)."""
import pytest

import agent
import models
from agent import Veronica
from test_veronica import FakeClaude, message, text


def test_the_jobs_have_the_models_from_the_owners_guide(monkeypatch):
    for name in models.ENV.values():
        monkeypatch.delenv(name, raising=False)
    assert models.chat_model() == 'anthropic/claude-sonnet-5.5'
    assert models.coding_model() == 'anthropic/claude-opus-5.5'
    assert models.research_model() == 'anthropic/claude-sonnet-5.5'
    assert models.helper_model() == 'xiaomi/mimo-v2.6-flash'        # owner, 2026-10-14: instead of Haiku 5.5
    assert models.backup_model() == 'google/gemini-3.1-flash-lite'
    assert not models.backup_model().startswith('anthropic/')      # a different maker is the point of a backup
    assert models.label('anthropic/claude-opus-5.5') == 'Claude Opus 5.5'


def test_env_lines_may_only_pick_a_listed_model(monkeypatch):
    monkeypatch.setenv('OPENROUTER_CODING_MODEL', 'anthropic/claude-sonnet-5.5')
    assert models.coding_model() == 'anthropic/claude-sonnet-5.5'
    for stray in ('qwen/qwen3.8-max-0902', 'openai/gpt-5', 'groq/llama', ''):
        monkeypatch.setenv('OPENROUTER_CODING_MODEL', stray)
        assert models.coding_model() == 'anthropic/claude-opus-5.5'
    monkeypatch.setenv('OPENROUTER_RESEARCH_MODEL', 'google/gemini-3.1-pro-preview')
    assert models.research_model() == 'google/gemini-3.1-pro-preview'


def test_after_two_failures_she_tries_the_backup_model(tmp_path, monkeypatch):
    import openai
    monkeypatch.setattr(agent.time, 'sleep', lambda s: None)
    boom = openai.APIConnectionError(request=None)
    veronica = Veronica(client=FakeClaude(boom, boom, message(text('Still here.'))),
                        projects_file=tmp_path / 'none.json')
    assert veronica.respond('hello').say == 'Still here.'
    assert [r['model'] for r in veronica.client.requests] == [
        'anthropic/claude-sonnet-5.5', 'anthropic/claude-sonnet-5.5', 'google/gemini-3.1-flash-lite']


def test_three_failures_are_said_plainly(tmp_path, monkeypatch):
    import openai
    monkeypatch.setattr(agent.time, 'sleep', lambda s: None)
    boom = openai.APIConnectionError(request=None)
    veronica = Veronica(client=FakeClaude(boom, boom, boom), projects_file=tmp_path / 'none.json')
    with pytest.raises(agent.VeronicaError, match='failed after 3 attempts'):
        veronica.respond('hello')


# ----- ASI:One (asi1-mini), the extra model (owner, 2026-10-14) -----------------------------------------------------

def test_asi1_mini_is_listed_and_served_by_asi_one(monkeypatch):
    assert 'asi1-mini' in models.ALLOWED and models.label('asi1-mini') == 'ASI:One Mini'
    assert models.endpoint('asi1-mini') == ('https://api.asi1.ai/v1', 'ASI_ONE_API_KEY')
    assert models.endpoint('anthropic/claude-sonnet-5.5') == ('https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY')
    thinking = {'reasoning': {'effort': 'minimal'}}
    assert models.extra_body('asi1-mini', thinking) is None             # OpenRouter-only settings never go to ASI:One
    assert models.extra_body('anthropic/claude-sonnet-5.5', thinking) == thinking
    monkeypatch.setenv('OPENROUTER_HELPER_MODEL', 'asi1-mini')
    assert models.helper_model() == 'asi1-mini'
    monkeypatch.setenv('OPENROUTER_HELPER_MODEL', 'asi-1-mini')          # the old spelling is not a model: ignored
    assert models.helper_model() == 'xiaomi/mimo-v2.6-flash'
    monkeypatch.setenv('OPENROUTER_HELPER_MODEL', 'anthropic/claude-haiku-5.5')   # Haiku stays one line away
    assert models.helper_model() == 'anthropic/claude-haiku-5.5'


def test_chat_on_asi1_mini_sends_no_openrouter_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_MODEL', 'asi1-mini')
    veronica = Veronica(client=FakeClaude(message(text('Hi from ASI.'))), projects_file=tmp_path / 'none.json')
    assert veronica.respond('hello').say == 'Hi from ASI.'
    request = veronica.client.requests[0]
    assert request['model'] == 'asi1-mini' and 'extra_body' not in request


def test_asi1_mini_without_its_key_is_said_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_MODEL', 'asi1-mini')
    monkeypatch.delenv('ASI_ONE_API_KEY', raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test-key')
    veronica = Veronica(projects_file=tmp_path / 'none.json')
    with pytest.raises(agent.VeronicaError, match='ASI_ONE_API_KEY'):
        veronica.respond('hello')
    monkeypatch.setenv('ASI_ONE_API_KEY', 'asi-test-key')
    client = veronica._client_for('asi1-mini')                          # made here; nothing is sent
    assert str(client.base_url).rstrip('/') == 'https://api.asi1.ai/v1' and client.api_key == 'asi-test-key'
    assert veronica._client_for('anthropic/claude-sonnet-5.5') is veronica.client
