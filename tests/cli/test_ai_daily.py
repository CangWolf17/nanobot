import importlib.util
import json
from pathlib import Path


SCRIPT = Path('/home/admin/.nanobot/cron/ai-daily.py')


def _load_module():
    spec = importlib.util.spec_from_file_location('ai_daily_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_daily_card_uses_interactive_markdown_shape(tmp_path):
    mod = _load_module()

    mod.get_issue_number = lambda date_str=None: 99
    mod.fetch_issue = lambda issue_number: ({
        'title': '今天 AI 有啥新东西',
        'html_url': 'https://example.com/issue/99',
        'created_at': '2026-04-14T01:23:45Z',
        'body': '## 概览\n- 第一条\n- 第二条\n### 小节\n继续看细节\n',
    }, None)

    card = mod.build_daily_card()

    assert card is not None
    assert card['title'] == '📰 AI 早报 #99'
    payload = json.loads(card['content'])
    assert payload['config']['wide_screen_mode'] is True
    markdown_blocks = [el for el in payload['elements'] if el.get('tag') == 'markdown']
    assert markdown_blocks
    assert '<at id="' in markdown_blocks[0]['content']
    assert '## 概览' in markdown_blocks[0]['content']
    assert '[GitHub Issue #99](https://example.com/issue/99)' in markdown_blocks[0]['content']


def test_ai_daily_module_defines_real_send_interactive_message():
    mod = _load_module()
    assert callable(getattr(mod, 'send_interactive_message', None))


def test_ai_daily_dry_run_ignores_sent_marker_and_does_not_overwrite_it(tmp_path):
    mod = _load_module()
    marker = tmp_path / '.ai-daily-sent'
    marker.write_text('2026-04-14', encoding='utf-8')
    log_file = tmp_path / 'ai-daily.log'

    mod.SENT_MARKER = str(marker)
    mod.LOG_FILE = str(log_file)

    built = {'title': '📰 AI 早报 #99', 'content': '{}'}
    captured = []

    mod.build_daily_card = lambda date_str=None: built
    mod.get_access_token = lambda: 'tok'
    mod.send_interactive_message = lambda token, payload: (captured.append((token, payload)) or True, None)

    mod.main(['--dry-run'])

    assert marker.read_text(encoding='utf-8') == '2026-04-14'
    assert captured == [('tok', built)]
    assert 'dry-run' in log_file.read_text(encoding='utf-8')


def test_ai_daily_normal_run_skips_when_sent_marker_matches_today(tmp_path):
    mod = _load_module()
    marker = tmp_path / '.ai-daily-sent'
    marker.write_text('2026-04-14', encoding='utf-8')
    log_file = tmp_path / 'ai-daily.log'

    mod.SENT_MARKER = str(marker)
    mod.LOG_FILE = str(log_file)

    called = {'build': 0}

    def fake_build(date_str=None):
        called['build'] += 1
        return {'title': 'ignored', 'content': '{}'}

    mod.build_daily_card = fake_build

    class _FakeDatetime:
        @staticmethod
        def now():
            class _Now:
                def strftime(self, fmt):
                    return '2026-04-14' if fmt == '%Y-%m-%d' else 'Tue Apr 14 09:55:01 2026'
            return _Now()

    mod.datetime = _FakeDatetime
    mod.main([])

    assert called['build'] == 0
    assert '今日早报已发送，跳过' in log_file.read_text(encoding='utf-8')
