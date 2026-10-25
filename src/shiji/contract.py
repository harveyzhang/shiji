"""拾迹前后端契约：白名单、取值范围和校验。

后端返回调查结果前一律经过 normalize_scene 和 validate_case；字段含义见管理入口里的《拾迹_开工清单与契约补丁》。
"""

PRESETS = ('historic_room', 'street_station', 'neutral_desk')
KINDS = ('floor', 'wall', 'door_window', 'table', 'chair', 'bench', 'shelf', 'lamp', 'suitcase', 'signboard',
         'display_stand', 'artifact')
INPUT_TYPES = ('old_photo', 'object')
STATUSES = ('ok', 'text_only', 'insufficient', 'search_failed', 'refused')
CLAIM_STATUSES = ('supported', 'conflicting', 'inferred', 'unknown')
EVIDENCE_CLASSES = ('supported', 'inferred', 'filler')
PURPOSES = ('identify', 'verify', 'crosscheck', 'context')
OBSERVATION_SOURCES = ('image', 'user')
SOURCE_TYPES = ('government', 'archive', 'media', 'encyclopedia', 'other')
MAX_OBJECTS, MIN_HOTSPOTS = 8, 2
BACKDROP = ('floor', 'wall')
STAGE = ((-3, 3), (0, 2), (-3, 3))
LABEL_MAX, NOTE_MAX = 16, 80
CAPTION = '探索性历史场景，不等同于实物测绘或精确复原'
REQUIRED = ('caseId', 'inputType', 'status', 'warnings', 'observations', 'searches', 'sources', 'claims', 'scene')


def _number(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def _object(raw, index, source_ids, warnings):
    position = (list(raw.get('position') or []) + [0, 0, 0])[:3]
    evidence_class = raw.get('evidenceClass') if raw.get('evidenceClass') in EVIDENCE_CLASSES else 'filler'
    evidence_ids = [i for i in raw.get('evidenceIds') or [] if i in source_ids]
    if evidence_class == 'supported' and not evidence_ids:
        warnings.append(f'场景物件 {raw.get("id") or index} 没有可用来源，改标为推断。')
        evidence_class = 'inferred'
    if evidence_class == 'filler':
        evidence_ids = []
    return {'id': str(raw.get('id') or f'obj-{index}'),
            'kind': raw.get('kind') if raw.get('kind') in KINDS else 'artifact',
            'label': str(raw.get('label') or '')[:LABEL_MAX],
            'note': str(raw.get('note') or '')[:NOTE_MAX],
            'position': [_number(v, low, high, 0) for v, (low, high) in zip(position, STAGE)],
            'scale': _number(raw.get('scale', 1), .5, 2, 1),
            'rotationY': _number(raw.get('rotationY', 0), -36000, 36000, 0) % 360,
            'evidenceClass': evidence_class,
            'evidenceIds': evidence_ids,
            'hotspot': bool(raw.get('hotspot'))}


def normalize_scene(scene, source_ids, warnings):
    """Clamp a proposed scene into the whitelist; anything unknown degrades visibly instead of failing."""
    preset = scene.get('preset')
    if preset not in PRESETS:
        warnings.append(f'场景类型 {preset!r} 不在白名单，改用中性调查台。')
        preset = 'neutral_desk'
    objects, seen = [], set()
    for index, raw in enumerate((scene.get('objects') or [])[:MAX_OBJECTS], 1):
        item = _object(raw if isinstance(raw, dict) else {}, index, source_ids, warnings)
        if item['id'] in seen:
            item['id'] = f'obj-{index}'
        seen.add(item['id'])
        objects.append(item)
    hot = sum(o['hotspot'] for o in objects)
    for wanted in EVIDENCE_CLASSES:  # Prefer evidence-backed objects as the clickable ones; the floor and walls never are.
        for item in objects:
            if hot < MIN_HOTSPOTS and not item['hotspot'] and item['evidenceClass'] == wanted and item['kind'] not in BACKDROP:
                item['hotspot'] = True
                hot += 1
    return {'preset': preset, 'caption': CAPTION, 'objects': objects}


def validate_case(case):
    """Return a list of contract violations; an empty list means the front end can render it."""
    errors = [f'缺少字段 {key}' for key in REQUIRED if key not in case]
    if errors:
        return errors

    def check(value, allowed, where):
        if value not in allowed:
            errors.append(f'{where} 的值 {value!r} 不在白名单 {allowed} 里')

    check(case['inputType'], INPUT_TYPES, 'inputType')
    check(case['status'], STATUSES, 'status')
    for item in case['observations']:
        check(item.get('source'), OBSERVATION_SOURCES, 'observations.source')
    ids = [source.get('id') for source in case['sources']]
    if len(ids) != len(set(ids)):
        errors.append('sources 的 id 有重复')
    known = set(ids)
    for source in case['sources']:
        if not str(source.get('url') or '').startswith(('https://', 'http://')):
            errors.append(f'来源 {source.get("id")} 的 url 不是网页链接')
        if 'type' in source:  # Older archived cases have no type; new ones always do.
            check(source['type'], SOURCE_TYPES, f'来源 {source.get("id")} 的 type')

    def cited(refs, where):
        errors.extend(f'{where} 引用了不存在的来源 {ref}' for ref in refs or [] if ref not in known)

    for search in case['searches']:
        check(search.get('purpose'), PURPOSES, 'searches.purpose')
        cited(search.get('sourceIds'), f'搜索「{search.get("query")}」')
    for claim in case['claims']:
        check(claim.get('status'), CLAIM_STATUSES, f'claims[{claim.get("id")}].status')
        cited(claim.get('evidenceIds'), f'判断 {claim.get("id")}')
        for view in claim.get('views') or []:
            cited(view.get('evidenceIds'), f'判断 {claim.get("id")} 的说法「{view.get("text")}」')
        if claim.get('status') == 'supported' and not claim.get('evidenceIds'):
            errors.append(f'判断 {claim.get("id")} 标为有证据支持，却没有来源')
    scene = case['scene']
    check(scene.get('preset'), PRESETS, 'scene.preset')
    objects = scene.get('objects') or []
    if len(objects) > MAX_OBJECTS:
        errors.append(f'场景物件超过 {MAX_OBJECTS} 个')
    if sum(bool(item.get('hotspot')) for item in objects) < MIN_HOTSPOTS:
        errors.append(f'场景热点少于 {MIN_HOTSPOTS} 个')
    for item in objects:
        check(item.get('kind'), KINDS, f'scene.objects[{item.get("id")}].kind')
        check(item.get('evidenceClass'), EVIDENCE_CLASSES, f'scene.objects[{item.get("id")}].evidenceClass')
        cited(item.get('evidenceIds'), f'场景物件 {item.get("id")}')
    return errors
