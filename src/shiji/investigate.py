"""一次调查：拒查 → 观察 → 三次目的不同的元宝搜索 → 来源 → 判断 → 场景。

判断和场景先按规则生成，每条结论都只引用这次真实搜索到的来源；接上大模型后只替换措辞，不替换依据。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
import secrets
from urllib.parse import urlparse

from .contract import INPUT_TYPES, normalize_scene, validate_case

QUERY_PLANS = {
    'object': (('identify', '{term}'), ('verify', '{term} 历史 年代'), ('context', '{term} 当年 使用 场景')),
    'old_photo': (('identify', '{term} 老照片'), ('verify', '{term} 历史'), ('context', '{term} 当年 环境 布局')),
}
REFUSE_WORDS = ('这个人是谁', '这人是谁', '他是谁', '她是谁', '人是谁', '人脸', '长相', '身份证',
                '总书记', '主席', '总统', '选举', '外交部', '两会')
POLITICAL_WORDS = ('总书记', '习近平', '国家主席', '总理', '政治局', '两会', '外交部', '选举')
REFUSED = '拾迹只调查老照片和旧物的来历，不识别人物身份，也不碰时政内容。'
PERIODS = (('明代', ('明代', '明朝')),
           ('清代', ('清代', '清朝', '晚清', '清末')),
           ('民国', ('民国',)),
           ('新中国成立后', ('新中国', '建国后', '解放后', '计划经济')),
           ('改革开放后', ('改革开放', '八十年代', '九十年代', '80年代', '90年代')))
STATION_WORDS = ('车站', '火车', '站台', '候车', '铁路', '码头', '街', '巷', '路口', '桥')
PAGES_PER_SEARCH, TITLE_MAX, SNIPPET_MAX, WRITER_EXCERPT = 4, 80, 160, 400

LAYOUTS = {
    'historic_room': (('table', (1.4, 0, 0.6), '桌子'), ('chair', (2.2, 0, 1.3), '椅子'),
                      ('shelf', (-2.0, 0, -1.8), '货架'), ('wall', (0, 0, -2.6), '墙面')),
    'street_station': (('signboard', (-1.8, 0, -1.5), '站牌'), ('bench', (1.6, 0, -0.8), '长凳'),
                       ('suitcase', (1.0, 0, 0.9), '行李箱'), ('lamp', (-2.4, 0, 0.6), '路灯')),
    'neutral_desk': (('table', (0, 0, 0), '调查台'),),
}
EXTRAS = (('灯', 'lamp'), ('箱', 'suitcase'), ('柜', 'shelf'), ('椅', 'chair'), ('凳', 'bench'), ('牌', 'signboard'))
EXTRA_SPOT = {'historic_room': (1.4, 0.8, 0.6), 'street_station': (-0.8, 0, 1.6)}
CORE_SPOT = {'historic_room': (0, 0, 0), 'street_station': (0, 0, 0), 'neutral_desk': (0, 0.8, 0)}
CORE_NOTES = {'supported': '有来源支持，点开看依据。', 'inferred': '只有 1 个站点提到，属于推断。',
              'filler': '资料不足，只做展示，不还原年代和地点。'}
FILLER_NOTE = '体验补全，不是证据。'
# Evidence tiers: a conflict needs two publishers just like support, so finding one is not a weaker claim.
STRENGTH = {'supported': 2, 'conflicting': 2, 'inferred': 1, 'unknown': 0}


def out_of_scope(text):
    return any(word in text for word in REFUSE_WORDS)


def _tokens(term):
    return [t for t in re.split(r'[\s，,、/]+', term) if len(t) >= 2] or [term]


def _text(source):
    return source['title']+source['snippet']


PLACES = ('北京', '上海', '天津', '重庆', '广州', '深圳', '南京', '杭州', '苏州', '无锡', '宁波', '武汉', '西安', '成都', '沈阳',
          '大连', '哈尔滨', '长春', '青岛', '济南', '郑州', '长沙', '福州', '厦门', '昆明', '贵阳', '南宁', '南昌', '合肥', '太原',
          '石家庄', '兰州', '呼和浩特', '乌鲁木齐', '拉萨', '银川', '西宁', '海口', '香港', '澳门', '台北', '河北', '山西', '辽宁',
          '吉林', '黑龙江', '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '海南', '四川', '贵州',
          '云南', '陕西', '甘肃', '青海', '台湾', '内蒙古', '广西', '西藏', '宁夏', '新疆')


def _mentioned(token, text):
    """A term like 北京前门火车站 also matches a source naming both 北京 and 前门火车站, if the rest is 3+ characters."""
    if token in text:
        return True
    for place in PLACES:
        rest = token[len(place):].lstrip('市省')
        if token.startswith(place) and len(rest) >= 3 and place in text and rest in text:
            return True
    for _, words in PERIODS:  # 清代女子服饰: any name of the period, plus 女子 or 服饰, since sources rarely use the exact phrase.
        for word in words:
            rest = token[len(word):]
            if token.startswith(word) and len(rest) >= 2 and any(w in text for w in words) \
                    and any(rest[i:i+2] in text for i in range(0, len(rest)-1, 2)):
                return True
    return False


def _mentions(source, tokens):
    return any(_mentioned(token, _text(source)) for token in tokens)


SUFFIX2 = {'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn', 'com.tw', 'org.tw', 'com.hk', 'org.hk', 'co.uk', 'co.jp'}
WIKI_MARKERS = ('维基百科', 'Wikipedia', 'wikipedia')


def publisher(source):
    """Who actually published it: Wikipedia mirrors are one source, and so are subdomains of one site."""
    host = (urlparse(source['url']).hostname or '').lower()
    if any(m in source['title'] for m in WIKI_MARKERS) or 'wiki' in host or 'pedia' in host:
        return 'wikipedia'
    labels = host.removeprefix('www.').split('.')
    if len(labels) >= 3 and '.'.join(labels[-2:]) in SUFFIX2:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


ARCHIVE_WORDS = ('档案', '方志', '博物馆', '纪念馆', '图书馆')
MEDIA_HOSTS = ('people.com.cn', 'peopleapp.com', 'xinhuanet.com', 'news.cn', 'cctv.com', 'cctv.cn', 'cnr.cn', 'gmw.cn',
               'chinanews.com.cn', 'chinanews.com', 'thepaper.cn', 'ce.cn', 'china.com.cn', 'chinadaily.com.cn',
               'legaldaily.com.cn', 'jschina.com.cn', 'xhby.net', 'hubpd.com', 'youth.cn', 'cyol.com', 'studytimes.cn')
MEDIA_WORDS = ('日报', '晚报', '新闻', '电视台', '广播', '人民网', '新华', '央视', '央广', '光明网', '澎湃')


def source_type(url, site):
    """Archive / government / media / encyclopedia / other, so the report can show how solid each source is."""
    host = (urlparse(url).hostname or '').lower()
    if any(word in site for word in ARCHIVE_WORDS) or 'archives' in host or 'museum' in host:
        return 'archive'
    if host.endswith(('.gov.cn', '.gov', '.gov.hk', '.gov.tw')):
        return 'government'
    if any(host == h or host.endswith('.'+h) for h in MEDIA_HOSTS) or any(word in site for word in MEDIA_WORDS):
        return 'media'
    if 'baike' in host or 'wiki' in host or 'pedia' in host or '百科' in site or '维基' in site:
        return 'encyclopedia'
    return 'other'


def _distinct_sites(sources, limit):
    picked, seen = [], set()
    for source in sources:
        key = publisher(source)
        if key not in seen:
            picked.append(source)
            seen.add(key)
    return picked[:limit]


class _Sources:
    """Collects sources across searches: one id per URL; political pages and dead links never enter."""

    def __init__(self, check_link):
        self.items, self.by_url, self.dropped_political, self.check_link = [], {}, set(), check_link
        self.purpose, self.passages = {}, {}  # first search purpose and a longer excerpt per source, for the writer

    def take(self, pages, purpose):
        candidates = []
        for page in pages:
            url = str(page.get('url') or '')
            if not url.startswith(('https://', 'http://')):
                continue
            text = str(page.get('title') or '')+str(page.get('passage') or page.get('content') or '')
            if any(word in text for word in POLITICAL_WORDS):
                self.dropped_political.add(url)
                continue
            candidates.append(page)
        if self.check_link:
            fresh = [p['url'] for p in candidates if p['url'] not in self.by_url]
            with ThreadPoolExecutor(max_workers=8) as pool:
                dead = {url for url, alive in zip(fresh, pool.map(self.check_link, fresh)) if alive is False}
            candidates = [p for p in candidates if p['url'] not in dead]
        ids = []
        for page in candidates[:PAGES_PER_SEARCH]:
            url = page['url']
            if url not in self.by_url:
                source = {'id': f'src-{len(self.items)+1}', 'title': str(page.get('title') or '')[:TITLE_MAX], 'url': url,
                          'site': str(page.get('site') or urlparse(url).netloc),
                          'publishedAt': page.get('date') or None,
                          'snippet': str(page.get('passage') or page.get('content') or '')[:SNIPPET_MAX]}
                source['type'] = source_type(url, source['site'])
                source['publisher'] = publisher(source)
                self.by_url[url] = source
                self.items.append(source)
                self.purpose[source['id']] = purpose
                self.passages[source['id']] = str(page.get('passage') or page.get('content') or '')[:WRITER_EXCERPT]
            ids.append(self.by_url[url]['id'])
        return ids

    def get(self, ids):
        lookup = {source['id']: source for source in self.items}
        return [lookup[i] for i in ids]

    def brief(self):
        """What the writer may read: only this case's sources, each with the search it came from."""
        return [{'id': s['id'], 'purpose': self.purpose[s['id']], 'site': s['site'], 'title': s['title'],
                 'snippet': self.passages[s['id']]} for s in self.items]


def _identity(input_type, term, tokens, sources):
    picked = _distinct_sites([s for s in sources if _mentions(s, tokens)], 3)
    subject = '照片里的场景' if input_type == 'old_photo' else '这件旧物'
    if len(picked) >= 2:
        return 'supported', f'{subject}与「{term}」的公开记录相符（{len(picked)} 个不同站点）。', picked
    if picked:
        return 'inferred', f'可能与「{term}」有关，目前只有 1 个站点提到，属于推断。', picked
    return 'unknown', f'没有找到「{term}」的直接记录，暂不判断。', []


def _era(sources):
    sites_by_period = {}
    for name, words in PERIODS:
        hits = _distinct_sites([s for s in sources if any(w in _text(s) for w in words)], 4)
        if hits:
            sites_by_period[name] = hits
    strong = [name for name, hits in sites_by_period.items() if len(hits) >= 2]
    if len(strong) >= 2:
        a, b = strong[:2]
        return 'conflicting', f'资料里的年代说法不一致：{a}、{b}。', sites_by_period[a][:2]+sites_by_period[b][:2]
    if strong:
        hits = sites_by_period[strong[0]]
        return 'supported', f'资料把它和「{strong[0]}」联系在一起（{len(hits)} 个不同站点）。', hits
    if sites_by_period:
        name, hits = next(iter(sites_by_period.items()))
        return 'inferred', f'只有 1 个站点提到「{name}」，年代待核实。', hits[:1]
    return 'unknown', '没有查到可靠的年代线索。', []


def _context(tokens, sources):
    picked = _distinct_sites([s for s in sources if _mentions(s, tokens)], 3)
    if picked:
        return 'inferred', f'当年的使用环境可参考《{picked[0]["title"][:24]}》等 {len(picked)} 条资料。', picked
    return 'unknown', '没有查到当年怎么用、放在哪里的资料。', []


def _claim(number, status, text, picked):
    return {'id': f'claim-{number}', 'text': text, 'status': status, 'evidenceIds': [s['id'] for s in picked]}


QUESTION_WORDS = ('？', '?', '吗', '什么', '怎么', '如何', '哪', '为什么', '是不是', '是否')
# Words that describe the upload rather than name anything; searching them finds articles about old photos in general.
GENERIC_NAMES = {'老照片', '旧照片', '照片', '图片', '合影', '合照', '人物', '人物合影', '老物件', '旧物', '物件', '古董', '文物', '老东西'}
YEAR = re.compile(r'(1[89]\d{2})\s*年')


def _claims_in(text):
    """Years and periods the user asserted, to be checked rather than searched as part of a name."""
    return [f'{y}年' for y in YEAR.findall(text) if int(y) < 1990] + [n for n, words in PERIODS if any(w in text for w in words)]


def _read_hint(hint):
    """Split the clue box into a name worth searching and the claims to check; a question is never a name."""
    claims = _claims_in(hint)
    if any(word in hint for word in QUESTION_WORDS):
        return '', claims
    name = YEAR.sub('', hint)
    for _, words in PERIODS:
        for word in words:
            name = re.sub(f'{word}(?:(?:时期|年间|时候)的?|的)', '', name)
    name = re.sub(r'^(这是|这个是|应该是|可能是|大概是|据说是|好像是)', '', name.strip()).strip('的 ，,。')
    return (name if len(name) >= 2 and name not in GENERIC_NAMES else ''), claims


def _years(sources):
    """Years (before 1990) mentioned in the sources, most publishers first: [('1906年', {publishers})]."""
    by_year = {}
    for source in sources:
        for year in set(YEAR.findall(_text(source))):
            if int(year) < 1990:
                by_year.setdefault(f'{year}年', set()).add(publisher(source))
    return sorted(by_year.items(), key=lambda item: (-len(item[1]), item[0]))


def _check_target(term, claims, identify_sources):
    """The verify search checks the user's own claim first, else the year the first search kept running into."""
    if claims:
        return f'{term} {claims[0]}', f'核验你说的「{claims[0]}」'
    years = _years(identify_sources)
    if years:
        year, publishers = years[0]
        return f'{term} {year}', f'来历搜索里有 {len(publishers)} 个发布方提到 {year}，换个问法核对这个年份'
    return None


def _views(raw, sources):
    """The writer's competing versions of a disputed fact, each kept only with sources from this case."""
    known = {s['id'] for s in sources.items}
    views = [{'text': view['text'], 'evidenceIds': [i for i in view['evidenceIds'] if i in known]} for view in raw or []]
    views = [view for view in views if view['text'] and view['evidenceIds']]
    return views if len(views) >= 2 else []


def _next_step(part, status, term, has_image):
    if part == 'identity':
        if status == 'supported':
            return f'点开来源，对照资料里的图片和文字，确认说的就是「{term}」。'
        if status == 'inferred':
            return f'目前只有 1 个发布方，再找一个独立来源核对「{term}」。'
        return ('补拍背面、底部或局部的文字和标记，写下拍摄地点或家里人的说法，再查一次。' if has_image
                else '补充更准的名称、地点或年代，再查一次。')
    if status == 'unknown':
        return '先查清它是什么，再查年代。' if part == 'era' else '先查清它是什么，再还原当年环境。'
    if part == 'era':
        return {'conflicting': '找带确切日期的档案、方志或报纸原文，看各个说法分别指哪件事。',
                'supported': '找一份档案或方志原文，确认这个年代。'}.get(status, '目前只有 1 个发布方提到，再找一个独立来源。')
    return '对照当年的照片或回忆文章，确认摆放和用法。'


def _regrade(number, draft, fallback, sources, tokens=None, conflict=False):
    """Use the writer's words only if its citations hold up; how strong the claim is comes from distinct publishers."""
    text = str((draft or {}).get('text') or '').strip()
    if not text or any(word in text for word in POLITICAL_WORDS):
        return fallback
    lookup = {s['id']: s for s in sources.items}
    cited = [lookup[i] for i in draft.get('evidenceIds') or [] if i in lookup]
    if tokens:
        cited = [s for s in cited if _mentions(s, tokens)]
    cited = _distinct_sites(cited, 4)
    if not cited:
        return fallback
    status = 'conflicting' if conflict and len(cited) >= 2 else ('supported' if len(cited) >= 2 else 'inferred')
    if STRENGTH[status] < STRENGTH[fallback['status']]:  # The writer may reword or strengthen a claim, never weaken it.
        return fallback
    return _claim(number, status, text, cited)


def _object(oid, kind, spot, label, note, evidence_class='filler', evidence_ids=(), hotspot=False, scale=1):
    return {'id': oid, 'kind': kind, 'label': label, 'note': note, 'position': list(spot), 'scale': scale,
            'rotationY': 0, 'evidenceClass': evidence_class, 'evidenceIds': list(evidence_ids), 'hotspot': hotspot}


def _scene(input_type, term, identity, context, has_image):
    if identity['status'] == 'unknown':
        preset = 'neutral_desk'
    elif any(word in term for word in STATION_WORDS):
        preset = 'street_station'
    else:
        preset = 'historic_room'
    core_class = identity['status'] if identity['status'] in ('supported', 'inferred') else 'filler'
    label = ('这张老照片' if input_type == 'old_photo' else '你上传的这件旧物') if has_image else f'「{term}」'
    objects = [_object('obj-floor', 'floor', (0, 0, 0), '地面', FILLER_NOTE, scale=2),
               _object('obj-core', 'display_stand', CORE_SPOT[preset], label, CORE_NOTES[core_class], core_class,
                       identity['evidenceIds'] if core_class != 'filler' else (), hotspot=True)]
    context_ids = context['evidenceIds']
    context_note = f'按「当年环境」检索到的资料摆放：{context["text"]}'
    for index, (kind, spot, name) in enumerate(LAYOUTS[preset]):
        linked = index == 0 and bool(context_ids) and preset != 'neutral_desk'
        objects.append(_object(f'obj-{kind}', kind, spot, name, context_note if linked else FILLER_NOTE,
                               'inferred' if linked else 'filler', context_ids if linked else (), hotspot=linked))
    if preset in EXTRA_SPOT:
        present = {o['kind'] for o in objects}
        for char, kind in EXTRAS:
            if char in term and kind not in present:
                objects.append(_object(f'obj-{kind}-term', kind, EXTRA_SPOT[preset], f'{term}（示意）',
                                       '形象示意，不是精确建模。'+(context_note if context_ids else ''),
                                       'inferred' if context_ids else 'filler', context_ids, hotspot=True))
                break
    return {'preset': preset, 'objects': objects}


WRITER_KINDS = ('table', 'chair', 'bench', 'shelf', 'lamp', 'suitcase', 'signboard', 'door_window', 'artifact')
SLOTS = {'historic_room': ((1.4, 0, 0.6), (-1.6, 0, 0.5), (2.2, 0, 1.3), (-2.0, 0, -1.8)),
         'street_station': ((-1.8, 0, -1.5), (1.6, 0, -0.8), (2.3, 0, 0.9), (-2.4, 0, 0.6))}
ON_TABLE = ('lamp', 'artifact')
TABLE_OFFSETS = (0, -0.45, 0.45)  # Up to three things share one table side by side.
FILL = {'historic_room': (('table', '桌子'), ('chair', '椅子'), ('shelf', '货架')),
        'street_station': (('bench', '长凳'), ('suitcase', '行李箱'), ('lamp', '路灯'), ('signboard', '站牌'))}
MIN_PROPS = 3
TALL = ('shelf', 'door_window', 'signboard', 'lamp')  # A lamp on the floor is drawn as a street or floor lamp.


def _take(slots, kind):
    """Tall things take the backmost free slot and short ones the frontmost, so nothing tall hides the rest."""
    return slots.pop(0 if kind in TALL else -1)


def _named_kind(kind, label):
    """The model often files a lamp or a case under artifact; its name says which component to draw."""
    if kind == 'artifact':
        for char, named in EXTRAS:
            if char in label:
                return named
    return kind


def _scene_from_writer(suggestion, input_type, term, identity, has_image, sources):
    """Place the model's whitelisted picks; only components citing this case's sources count as inferred."""
    preset = (suggestion or {}).get('preset')
    if identity['status'] == 'unknown' or preset not in SLOTS:
        return None
    lookup = {s['id'] for s in sources.items}
    core_class = identity['status'] if identity['status'] in ('supported', 'inferred') else 'filler'
    label = ('这张老照片' if input_type == 'old_photo' else '你上传的这件旧物') if has_image else f'「{term}」'
    objects = [_object('obj-floor', 'floor', (0, 0, 0), '地面', FILLER_NOTE, scale=2),
               _object('obj-core', 'display_stand', CORE_SPOT[preset], label, CORE_NOTES[core_class], core_class,
                       identity['evidenceIds'] if core_class != 'filler' else (), hotspot=True)]
    if preset == 'historic_room':
        objects.append(_object('obj-wall', 'wall', (0, 0, -2.6), '墙面', FILLER_NOTE))
    slots, table, on_table, picked = sorted(SLOTS[preset], key=lambda spot: spot[2]), None, 0, 0
    for index, item in enumerate(suggestion.get('objects') or []):
        name = item.get('label') or ''
        kind = _named_kind(item.get('kind'), name)
        if kind not in WRITER_KINDS:
            continue
        if kind in ON_TABLE and not table and preset == 'historic_room' and slots:  # Indoors, small things get a table first.
            table = _take(slots, 'table')
            objects.append(_object('obj-table', 'table', table, '桌子', FILLER_NOTE))
        if kind in ON_TABLE and table and on_table < len(TABLE_OFFSETS):
            spot, on_table = (table[0] + TABLE_OFFSETS[on_table], 0.8, table[2]), on_table+1
        elif slots:
            spot = _take(slots, kind)
            table = spot if kind == 'table' and not table else table
        else:
            continue
        cited = [i for i in item.get('evidenceIds') or [] if i in lookup]
        note = item.get('note') or '资料里提到这一点。' if cited else FILLER_NOTE  # Uncited wording is never shown as fact.
        objects.append(_object(f'obj-w{index}', kind, spot, name or kind, note,
                               'inferred' if cited else 'filler', cited, hotspot=bool(cited)))
        picked += 1
    if not picked:
        return None
    present = {o['kind'] for o in objects}
    for kind, name in FILL[preset]:  # A bare scene is filled with common props, all marked as filler.
        if sum(o['kind'] not in ('floor', 'wall') for o in objects) - 1 >= MIN_PROPS or not slots:
            break
        if kind not in present:
            objects.append(_object(f'obj-fill-{kind}', kind, _take(slots, kind), name, FILLER_NOTE))
    return {'preset': preset, 'objects': objects}


def _tell(progress, event, data):
    if progress is None:
        return
    try:
        progress(event, data)
    except Exception:  # A listener that went away must never break the investigation.
        pass


def investigate(input_type, hint, search, *, image=None, observe=None, write=None, check_link=None, case_id=None,
                progress=None):
    if input_type not in INPUT_TYPES:
        raise ValueError(f'inputType 只能是 {INPUT_TYPES}')
    hint = (hint or '').strip()
    warnings, observations, searches = [], [], []
    case = {'caseId': case_id or f'case-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}', 'inputType': input_type,
            'status': 'ok', 'warnings': warnings, 'observations': observations, 'searches': searches, 'sources': [],
            'claims': [], 'scene': {}}
    if hint:
        observations.append({'text': hint[:60], 'source': 'user', 'confirmed': False})
    if out_of_scope(hint):
        return _stop(case, 'refused', REFUSED)

    name, claims = _read_hint(hint)
    term, observed = name, False
    if image is None:
        warnings.append('没有上传图片，本次根据文字线索调查。')
    elif observe is None:
        warnings.append('识图还没接入，本次根据文字线索调查。')
    else:
        _tell(progress, 'observe', {})
        try:
            seen = observe(image, input_type) or {}
            found = [{'text': str(t)[:60], 'source': 'image', 'confirmed': False} for t in seen.get('observations') or []]
            observations[:0] = found
            observed = bool(found)
            term = term or str(seen.get('term') or '').strip()
            _tell(progress, 'observed', {'observations': [o['text'] for o in found], 'term': term})
        except Exception as exc:  # Our RuntimeErrors are key-free; other errors only show their type.
            reason = str(exc)[:40].rstrip('。') if isinstance(exc, RuntimeError) else type(exc).__name__
            warnings.append(f'识图失败（{reason}），本次根据文字线索调查。')
            _tell(progress, 'observed', {'observations': [], 'term': term, 'failed': True})
    if out_of_scope(term):
        return _stop(case, 'refused', REFUSED)
    if not term:
        return _stop(case, 'insufficient', '没有可以搜索的线索，请补充一句文字，比如物件名称、地点或年代。')

    sources, found = _Sources(check_link), {}

    def run(purpose, query, reason):
        _tell(progress, 'search', {'purpose': purpose, 'query': query, 'reason': reason})
        try:
            ids = sources.take(search(query), purpose)
            searches.append({'purpose': purpose, 'query': query, 'reason': reason, 'ok': True, 'sourceIds': ids})
        except Exception as exc:  # A failed search is shown as failed, never as an empty success.
            warnings.append(f'第 {len(searches)+1} 次搜索失败：{str(exc)[:40]}')
            searches.append({'purpose': purpose, 'query': query, 'reason': reason, 'ok': False, 'sourceIds': []})
        found[purpose] = sources.get(searches[-1]['sourceIds'])
        _tell(progress, 'searched', {'purpose': purpose, 'query': query, 'ok': searches[-1]['ok'],
                                     'count': len(searches[-1]['sourceIds'])})

    if search is None:
        warnings.append('搜索已关闭，本次不下结论。')
    else:
        plan = dict(QUERY_PLANS[input_type])
        run('identify', plan['identify'].format(term=term), f'用{"你给的线索" if name else "识图"}得到的「{term}」找直接记录')
        run('verify', *(_check_target(term, claims, found['identify'])
                        or (plan['verify'].format(term=term), '换个问法复核来历和年代')))
        strong = [year for year, publishers in _years(found['identify']+found['verify']) if len(publishers) >= 2]
        if len(strong) >= 2:
            run('crosscheck', f'{term} {strong[0]} {strong[1]}',
                f'资料里{strong[0]}和{strong[1]}都有两个以上发布方提到，再查一次它们分别指什么、有没有矛盾')
        run('context', plan['context'].format(term=term), '查当年的环境和用法，用来搭历史现场')
    if sources.dropped_political:
        warnings.append(f'已过滤 {len(sources.dropped_political)} 条涉及时政的来源。')
    case['sources'] = sources.items

    tokens = _tokens(term)
    checked = found.get('verify', [])+found.get('crosscheck', [])
    identity = _claim(1, *_identity(input_type, term, tokens, found.get('identify', [])+checked))
    era = _claim(2, *_era(checked))
    context = _claim(3, *_context(tokens, found.get('context', [])))
    drafts = {}
    if write is not None and any(s['ok'] for s in searches):
        _tell(progress, 'write', {})
        try:
            drafts = write(term, input_type, [o['text'] for o in observations], sources.brief())
            era_draft = drafts.get('era') or {}
            identity, era, context = (_regrade(1, drafts.get('identity'), identity, sources, tokens),
                                      _regrade(2, era_draft, era, sources, conflict=bool(era_draft.get('conflict'))),
                                      _regrade(3, drafts.get('context'), context, sources))
        except Exception as exc:  # The rule-based claims stay; the reason is shown, never hidden.
            reason = str(exc)[:40].rstrip('。') if isinstance(exc, RuntimeError) else type(exc).__name__
            warnings.append(f'归纳失败（{reason}），使用规则版判断。')
    era_views = _views((drafts.get('era') or {}).get('views'), sources) if era['status'] == 'conflicting' else []
    if identity['status'] == 'unknown':  # Until we know what it is, its era and setting stay open.
        era = _claim(2, 'unknown', '还没查清它是什么，暂不判断年代。', [])
        context = _claim(3, 'unknown', '还没查清它是什么，暂不还原当年环境。', [])
        era_views = []
    for part, claim in zip(('identity', 'era', 'context'), (identity, era, context)):
        claim['views'] = era_views if part == 'era' else []
        claim['nextStep'] = _next_step(part, claim['status'], term, image is not None)
    case['claims'] = [identity, era, context]
    if not any(s['ok'] for s in searches):
        case['status'] = 'search_failed'
    elif identity['status'] == 'unknown':
        case['status'] = 'insufficient'
    elif not observed:
        case['status'] = 'text_only'
    known = {s['id'] for s in sources.items}
    scene = (_scene_from_writer(drafts.get('scene'), input_type, term, identity, image is not None, sources)
             or _scene(input_type, term, identity, context, image is not None))
    _tell(progress, 'scene', {'preset': scene['preset']})
    return _finish(case, known, scene)


def _stop(case, status, message):
    case['status'] = status
    case['warnings'].append(message)
    return _finish(case, set(), {'preset': 'neutral_desk', 'objects': []})


def _finish(case, known, scene):
    if not scene['objects']:
        scene = {'preset': 'neutral_desk', 'objects': [
            _object('obj-desk', 'table', (0, 0, 0), '调查台', FILLER_NOTE),
            _object('obj-core', 'display_stand', CORE_SPOT['neutral_desk'], '待调查的对象', CORE_NOTES['filler'], hotspot=True)]}
    case['scene'] = normalize_scene(scene, known, case['warnings'])
    case['warnings'].extend(f'内部校验：{error}' for error in validate_case(case))
    return case
