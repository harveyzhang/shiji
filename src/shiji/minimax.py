"""MiniMax Token Plan：归纳走 OpenAI 兼容的对话接口，识图走 Token Plan 专用的看图接口。

这把 key 是国内区，只能用 api.minimax.cn（api.minimax.com 解析不了，api.minimax.io 返回 401）。
Toklens 的免费额度是所有未付费用户共用的，高峰期会连续 429；所以配了 MiniMax 就先用它，失败了再退到 Toklens。
"""
import os
from pathlib import Path

from .vision import SYSTEM, ToklensVision
from .writer import ToklensWriter

BASE_URL = 'https://api.minimax.cn/v1'
TEXT_MODEL = 'MiniMax-M3.1-Flash'  # 2026-10-25 实测：JSON 干净，推理过程单独放在 reasoning_content


class MiniMaxWriter(ToklensWriter):
    def __init__(self, key_file=None, **options):
        super().__init__(key_file or os.environ.get('SHIJI_MINIMAX_KEY_FILE'), base_url=BASE_URL,
                         model=os.environ.get('SHIJI_MINIMAX_MODEL') or TEXT_MODEL, **options)


class MiniMaxVision(ToklensVision):
    def __init__(self, key_file=None, **options):
        super().__init__(key_file or os.environ.get('SHIJI_MINIMAX_KEY_FILE'), base_url=BASE_URL, model='coding_plan/vlm', **options)

    def look(self, text, url):
        response = self.post('/coding_plan/vlm', {'prompt': f'{SYSTEM}\n{text}', 'image_url': url},
                             headers={'MM-API-Source': 'Minimax-MCP'})
        try:
            data = response.json()
            code, content = (data.get('base_resp') or {}).get('status_code', 0), data.get('content') or ''
        except (ValueError, AttributeError):
            raise RuntimeError(f'{self.label}返回了无法解析的结果。') from None
        if code != 0:  # The status message may echo request details, so only the code is shown.
            raise RuntimeError(f'{self.label}请求失败（MiniMax {code}）。')
        return self.json_in(content)


class FirstThatWorks:
    """Ask each model in order; the next one answers when one fails, e.g. a shared quota running dry."""

    def __init__(self, models):
        self.models = list(models)

    def __call__(self, *args):
        error = RuntimeError('没有可用的模型。')
        for model in self.models:
            try:
                return model(*args)
            except Exception as exc:  # Timeouts and refused connections fall through to the next model too.
                error = exc
        raise error


def _key_file(variable):
    path = os.environ.get(variable)
    return path if path and Path(path).is_file() else None


def _chain(*choices):
    models = [cls(path) for cls, variable in choices if (path := _key_file(variable))]
    return FirstThatWorks(models) if models else None


def default_observe():
    return _chain((MiniMaxVision, 'SHIJI_MINIMAX_KEY_FILE'), (ToklensVision, 'SHIJI_VISION_KEY_FILE'))


def default_write():
    return _chain((MiniMaxWriter, 'SHIJI_MINIMAX_KEY_FILE'), (ToklensWriter, 'SHIJI_VISION_KEY_FILE'))
