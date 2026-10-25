"""OpenAI 兼容的对话接口（Toklens，也用于 MiniMax）：读 key、发请求、遇到 429 和 5xx 退避重试、取出回复里的 JSON。识图和归纳共用。"""
import json
import os
import re
import time
from pathlib import Path

import httpx

BASE_URL = 'https://lax.toklens.ai/v1'
DEFAULT_MODEL = 'gpt-5.6-luna'  # 2026-10-24 实测：认字比 gpt-6-luna 准，单张 5–15 秒
RETRY_STATUSES, ATTEMPTS = {429, 500, 502, 503, 504}, 3  # 实测连续两次请求会遇到 HTTP 429
KEY_NAMES = ('TOKLENS_API_KEY=', 'MINIMAX_API_KEY=')


def read_key(path):
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith(KEY_NAMES):
            value = line.split('=', 1)[1].strip().strip('"\'')
            if value:
                return value
    raise RuntimeError('模型 key 文件无效。')


def _wait(response, attempt):
    try:
        return min(float(response.headers.get('Retry-After', '')), 10.0)
    except ValueError:
        return 2.0*(attempt+1)


class ToklensChat:
    label = '模型'

    def __init__(self, key_file=None, *, model=None, base_url=None, transport=None, timeout=60, sleep=time.sleep):
        key_file = key_file or os.environ.get('SHIJI_VISION_KEY_FILE')
        if not key_file:
            raise RuntimeError(f'{self.label}需要 SHIJI_VISION_KEY_FILE。')
        self.key = read_key(key_file)
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get('SHIJI_VISION_BASE_URL') or BASE_URL).rstrip('/')
        self.transport, self.timeout, self.sleep = transport, timeout, sleep

    def __repr__(self):
        return f'{type(self).__name__}(model={self.model!r})'

    def post(self, path, body, headers=None):
        """POST with back-off retries on 429 and 5xx; errors never carry the key."""
        with httpx.Client(timeout=self.timeout, transport=self.transport, trust_env=False) as client:
            for attempt in range(ATTEMPTS):
                response = client.post(f'{self.base_url}{path}', json=body,
                                       headers={'Authorization': 'Bearer '+self.key, **(headers or {})})
                if response.status_code not in RETRY_STATUSES or attempt == ATTEMPTS-1:
                    break
                self.sleep(_wait(response, attempt))
        if response.is_error:
            raise RuntimeError(f'{self.label}请求失败（HTTP {response.status_code}）。')
        return response

    def json_in(self, content):
        try:
            return json.loads(re.search(r'\{.*\}', content, re.S).group(0))
        except (TypeError, ValueError, AttributeError):
            raise RuntimeError(f'{self.label}返回了无法解析的结果。') from None

    def ask_json(self, messages):
        response = self.post('/chat/completions', {'model': self.model, 'messages': messages})
        try:
            content = response.json()['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError(f'{self.label}返回了无法解析的结果。') from None
        return self.json_in(content)
