"""Regenerate mock/*.json for the front end: the real pipeline fed by a fake search, so mocks match real output.

Run: uv run python scripts/make_mocks.py
"""
import json
from pathlib import Path

from shiji.investigate import investigate

MOCKS = Path(__file__).resolve().parent.parent/'mock'
NOTICE = '开发模拟数据：不是真实搜索结果，不能用于提交。'


def page(slug, title, site, passage):
    # One reserved .invalid host per mock site, so mock sites count as different publishers like real ones.
    host = 'mock-'+site.removeprefix('模拟站点').lower()+'.invalid'
    return {'title': '【模拟】'+title, 'url': f'https://{host}/{slug}', 'site': site, 'passage': '【模拟】'+passage}


CASES = {
    'mock-object-lamp': ('object', '煤油灯', ['【模拟】玻璃灯罩', '【模拟】金属底座和旋钮'], {
        'identify': [page('lamp-1', '煤油灯的来历', '模拟站点A', '煤油灯是一种照明工具'),
                     page('lamp-2', '老物件里的煤油灯', '模拟站点B', '煤油灯')],
        'verify': [page('lamp-3', '煤油灯传入中国', '模拟站点A', '煤油灯清末传入，民国时期普及'),
                   page('lamp-4', '照明工具的变迁', '模拟站点C', '清代晚期的煤油灯')],
        'context': [page('lamp-5', '当年家里怎么用煤油灯', '模拟站点D', '煤油灯放在桌上照明')],
    }),
    'mock-photo-station': ('old_photo', '老火车站 候车室', ['【模拟】木质长椅', '【模拟】墙上有站牌'], {
        'identify': [page('station-1', '老火车站 候车室旧影', '模拟站点E', '老火车站 候车室'),
                     page('station-2', '候车室老照片', '模拟站点F', '候车室')],
        'verify': [page('station-3', '老火车站的历史', '模拟站点E', '民国时期的老火车站'),
                   page('station-4', '铁路记忆', '模拟站点G', '民国候车室')],
        'context': [page('station-5', '候车室当年的布局', '模拟站点H', '候车室有长凳和站牌')],
    }),
}
PURPOSE_WORDS = (('context', ('当年',)), ('verify', ('历史',)), ('identify', ('',)))


def fake_search(answers):
    def search(query, **params):
        for purpose, words in PURPOSE_WORDS:
            if any(word in query for word in words):
                return answers[purpose]
        return []
    return search


def main():
    MOCKS.mkdir(exist_ok=True)
    for case_id, (input_type, term, seen, answers) in CASES.items():
        observe = lambda image, kind, seen=seen, term=term: {'observations': seen, 'term': term}
        case = investigate(input_type, '', fake_search(answers), image=b'mock', observe=observe, case_id=case_id)
        case['warnings'].insert(0, NOTICE)
        (MOCKS/f'{case_id}.json').write_text(json.dumps(case, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(case_id, case['status'], case['scene']['preset'], len(case['sources']), 'sources')


if __name__ == '__main__':
    main()
