"""Test helpers for the research workflow (used by test_research.py): a scripted chat model that is safe across a
restart, fake search and fetch that log their side effects to a file, and a runner that crashes mid-task in a
separate process.

    python research_fakes.py crash DB CHECKPOINTS REPORTS LOG
"""
import hashlib
import os
import sys
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

PAGES = {
    'https://a.example/heat': ('Heat pumps explained',
                               'Heat pumps move heat rather than making it, so they can deliver three units of heat '
                               'for each unit of electricity in mild weather. ' * 3),
    'https://b.example/cold': ('Heat pumps in cold climates',
                               'In very cold weather the efficiency of air source heat pumps falls, although modern '
                               'units still work well below freezing. ' * 3),
}
BODY = ('Heat pumps are efficient because they move heat instead of generating it [1]. ' * 4 +
        'Their efficiency drops in very cold weather, but modern units keep working below freezing [2]. ' * 4)
GOOD = [{'source': 1, 'quote': 'they can deliver three units of heat for each unit of electricity'},
        {'source': 2, 'quote': 'the efficiency of air source heat pumps falls ... still work well below freezing'}]
BAD = [{'source': 1, 'quote': 'heat pumps are ten times better than gas boilers'}, GOOD[1]]


class ScriptedModel(BaseChatModel):
    """Answers with script[k], where k is the number of answers already in the conversation, so after a restart it
    carries on from where the conversation is. An Exception in the script is raised instead."""
    script: list[Any] = Field(default_factory=list)
    model_name: str = 'claude-opus-5-5'
    steps: list[int] = Field(default_factory=list)
    tools_seen: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return 'scripted-fake'

    def _get_ls_params(self, stop=None, **kwargs):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params['ls_provider'] = 'anthropic'
        params['ls_model_name'] = self.model_name
        return params

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if tool_choice is not None:
            kwargs['tool_choice'] = tool_choice
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        step = sum(1 for m in messages if isinstance(m, AIMessage))
        self.steps.append(step)
        self.tools_seen.append([t['function']['name'] for t in kwargs.get('tools') or []])
        item = self.script[step] if step < len(self.script) else AIMessage(content='(script exhausted)')
        if isinstance(item, BaseException):
            raise item
        answer = item.model_copy(deep=True)
        answer.usage_metadata = {'input_tokens': 1000, 'output_tokens': 200, 'total_tokens': 1200}
        answer.response_metadata = {'model_name': 'claude-opus-5-5',
                                    'stop_reason': 'tool_use' if answer.tool_calls else 'end_turn'}
        return ChatResult(generations=[ChatGeneration(message=answer)])


def call(name, n, **args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': f'call_{n}', 'type': 'tool_call'}])


def script(bad_first=True, final='Heat pumps work well in mild weather and still work below freezing.'):
    steps = [
        call('write_todos', 0, todos=[{'content': 'Search for sources', 'status': 'in_progress'},
                                      {'content': 'Write the report', 'status': 'pending'}]),
        call('web_search', 1, query='heat pumps cold climate'),
        call('fetch_source', 2, url='https://a.example/heat'),
        call('fetch_source', 3, url='https://b.example/cold'),
    ]
    if bad_first:
        steps.append(call('submit_report', 4, title='Heat pumps', report_markdown=BODY, citations_list=BAD))
    steps.append(call('submit_report', 5, title='Heat pumps', report_markdown=BODY, citations_list=GOOD))
    steps.append(AIMessage(content=final))
    return steps


class Effects:
    """Fake search and fetch that append one line per call to a file (flushed and fsynced, so a crash keeps it)."""

    def __init__(self, path, crash_on=None, fail=None):
        self.path, self.crash_on, self.fail = path, crash_on, dict(fail or {})

    def note(self, line):
        with open(self.path, 'a', encoding='utf-8') as out:
            out.write(line + '\n')
            out.flush()
            os.fsync(out.fileno())

    def lines(self):
        try:
            with open(self.path, encoding='utf-8') as source:
                return source.read().splitlines()
        except FileNotFoundError:
            return []

    def search(self, query, count=8):
        self.note(f'search {query}')
        return [{'title': title, 'url': url, 'description': text[:80]} for url, (title, text) in PAGES.items()]

    def fetch(self, url):
        self.note(f'fetch {url}')
        if url in self.fail:
            raise self.fail[url]
        if url == self.crash_on and 'crashed' not in self.lines():
            self.note('crashed')
            os._exit(3)          # the worker process dies in the middle of this tool call
        title, text = PAGES[url]
        return {'url': url, 'title': title, 'text': text, 'content_hash': hashlib.sha256(text.encode()).hexdigest()}


def crash_main(db_path, checkpoints, reports, log_path):
    import research
    import store
    import tasks
    db = store.Store(db_path)
    effects = Effects(log_path, crash_on='https://b.example/cold')
    tasks.worker(db=db, once=True, saver=research.checkpointer(__import__('pathlib').Path(checkpoints)),
                 model=ScriptedModel(script=script()), search=effects.search, fetch=effects.fetch,
                 reports_dir=reports)


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == 'crash':
        crash_main(*sys.argv[2:])
