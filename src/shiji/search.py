"""Yuanbao search with the team's quota ledger; every sent query is booked first.

Ported from zhiyan-detective so both works share one ledger format and one count.
"""
import fcntl
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

ENDPOINT = 'https://api.wsa.cloud.tencent.com/SearchPro'
MONTH_CAP, DAY_CAP, CACHE_TTL = 5000, 1200, 24*3600
STOP_CODES = ('ResourceUnavailable', 'ResourceInsufficient', 'RequestLimitExceeded')


class BudgetExceeded(RuntimeError):
    pass


class Pages(list):
    cached = False


def reserve(ledger, query, source='shiji', month_cap=MONTH_CAP, day_cap=DAY_CAP):
    # Same ledger and format as wsa_check.py, so all team tools share one count.
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, 'a+', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        text = fh.read().strip()
        data = json.loads(text) if text else {}
        now = datetime.now().astimezone()
        month, day = now.strftime('%Y-%m'), now.strftime('%Y-%m-%d')
        if data.get('month') != month:
            data = {'month': month, 'month_count': 0, 'days': {}, 'recent': []}
        if data['month_count']+1 > month_cap or data['days'].get(day, 0)+1 > day_cap:
            raise BudgetExceeded('今天的搜索额度用完了，明天再来。')
        data['month_count'] += 1
        data['days'][day] = data['days'].get(day, 0)+1
        data['recent'] = (data.get('recent', [])+[{'ts': now.isoformat(timespec='seconds'), 'query': query, 'source': source}])[-200:]
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(data, ensure_ascii=False, indent=1))
        fh.flush()


def read_key(path):
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith('TENCENTCLOUD_WSA_APIKEY='):
            value = line.split('=', 1)[1].strip()
            if value:
                return value
    raise RuntimeError('元宝搜索 key 文件无效。')


class YuanbaoSearch:
    def __init__(self, key_file=None, ledger=None, *, transport=None, timeout=15, cache_dir=None,
                 month_cap=MONTH_CAP, day_cap=DAY_CAP, source='shiji'):
        key_file = key_file or os.environ.get('SHIJI_WSA_KEY_FILE')
        ledger = ledger or os.environ.get('SHIJI_LEDGER')
        if not key_file or not ledger:
            # No default ledger: a second, silent ledger would split the team's quota count.
            raise RuntimeError('真实搜索需要 SHIJI_WSA_KEY_FILE 和 SHIJI_LEDGER；只看页面可设 SHIJI_SEARCH_DISABLED=1。')
        self.key, self.ledger = read_key(key_file), Path(ledger)
        self.cache = Path(cache_dir) if cache_dir else None
        self.transport, self.timeout, self.caps, self.source = transport, timeout, (month_cap, day_cap), source

    def __repr__(self):
        return 'YuanbaoSearch()'

    def __call__(self, query, **params):
        body = {'Query': query[:480], **params}  # Query is limited to 1500 UTF-8 bytes.
        name = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        hit = self._cached(name)
        if hit is not None:
            return hit
        reserve(self.ledger, query, source=self.source, month_cap=self.caps[0], day_cap=self.caps[1])
        with httpx.Client(timeout=self.timeout, transport=self.transport, trust_env=False) as client:
            response = client.post(ENDPOINT, json=body, headers={'Authorization': 'Bearer '+self.key})
        if response.is_error:
            raise RuntimeError(f'元宝搜索请求失败（HTTP {response.status_code}）。')
        try:
            data = response.json().get('Response') or {}
        except ValueError:
            raise RuntimeError('元宝搜索返回了无法解析的结果。') from None
        if data.get('Error'):
            code = str(data['Error'].get('Code') or 'Unknown')
            if code.startswith(STOP_CODES):
                raise BudgetExceeded(code)
            raise RuntimeError(f'元宝搜索返回错误（{code[:40]}）。')
        pages = Pages()
        for item in data.get('Pages') or []:
            try:
                page = json.loads(item) if isinstance(item, str) else item
            except ValueError:
                continue
            if isinstance(page, dict) and page.get('url'):
                pages.append(page)
        self._store(name, body, pages)
        return pages

    def _cached(self, name):
        if not self.cache:
            return None
        try:
            saved = json.loads((self.cache/f'{name}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if time.time()-saved.get('ts', 0) > CACHE_TTL:
            return None
        pages = Pages(saved.get('pages') or [])
        pages.cached = True
        return pages

    def _store(self, name, body, pages):
        if not self.cache:
            return
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = self.cache/f'.{name}.tmp'
        tmp.write_text(json.dumps({'ts': time.time(), 'body': body, 'pages': pages}, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.cache/f'{name}.json')
