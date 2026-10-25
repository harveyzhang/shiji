"""归纳：让模型只根据这次搜到的来源摘要，写三条判断（它是什么、年代、当年环境），并标出引用的来源。

模型只负责措辞和挑引用；「有证据支持」还是「推断」，由 investigate 按引用里不同发布方的数量判定，模型无权升级。
"""
import json
import os

from .toklens import ToklensChat

SYSTEM = ('你是拾迹的调查记录员。只能根据给你的来源摘要写判断，不能用来源以外的知识；不写人物身份，不谈时政。'
          '每条判断不超过 40 字，语气保守；来源之间说法不一致就如实说不一致，并把 conflict 设为 true。'
          'evidenceIds 只能填真正支持这句话的来源编号；资料里没有的，text 写「资料里没有提到」，evidenceIds 留空。'
          '来源对同一件事（比如同一座建筑的建成年份）说法不同，era 的 conflict 设为 true，并在 views 里分别列出每种说法和支持它的来源；'
          '不同年份说的是不同的事（比如始建和建成）不算冲突，views 留空。'
          '只输出 JSON：{"identity": {"text": "它是什么，或照片拍的是什么", "evidenceIds": []}, '
          '"era": {"text": "资料里关于年代的说法", "evidenceIds": [], "conflict": false, '
          '"views": [{"text": "其中一种说法，不超过 20 字", "evidenceIds": []}]}, '
          '"context": {"text": "当年怎么用、放在什么环境里", "evidenceIds": []}, '
          '"scene": {"preset": "historic_room 或 street_station", "objects": []}}。'
          'scene 用来搭一个微型历史现场：preset 选 historic_room（室内）或 street_station（街道、车站、建筑外景）；'
          'objects 最多 4 个，每个是 {"kind": 只能是 table、chair、bench、shelf、lamp、suitcase、signboard、door_window、artifact 之一, '
          '"label": "不超过 12 字", "note": "资料里为什么会这样摆，不超过 40 字", "evidenceIds": [支持它的来源编号，没有就留空]}。')
PARTS, TEXT_MAX, LABEL_MAX, MAX_SCENE_OBJECTS = ('identity', 'era', 'context'), 40, 12, 4
VIEW_MAX, MAX_VIEWS = 20, 3


def _ids(raw):
    """Source ids as this case writes them; some models answer with the bare number (2 for src-2)."""
    ids = [str(i).strip() for i in raw or []]
    return [f'src-{i}' if i.isdigit() else i for i in ids if i]


class ToklensWriter(ToklensChat):
    label = '归纳'

    def __init__(self, key_file=None, *, model=None, **options):
        super().__init__(key_file, model=model or os.environ.get('SHIJI_WRITER_MODEL'), **options)

    def __call__(self, term, input_type, observations, sources):
        payload = {'term': term, 'inputType': input_type, 'observations': observations, 'sources': sources}
        data = self.ask_json([{'role': 'system', 'content': SYSTEM},
                              {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}])
        drafts = {}
        for part in PARTS:
            item = data.get(part) if isinstance(data.get(part), dict) else {}
            drafts[part] = {'text': str(item.get('text') or '').strip()[:TEXT_MAX],
                            'evidenceIds': _ids(item.get('evidenceIds'))}
        era = data.get('era') if isinstance(data.get('era'), dict) else {}
        drafts['era']['conflict'] = bool(era.get('conflict'))
        drafts['era']['views'] = [{'text': str(view.get('text') or '').strip()[:VIEW_MAX],
                                   'evidenceIds': _ids(view.get('evidenceIds'))}
                                  for view in era.get('views') or [] if isinstance(view, dict) and view.get('text')][:MAX_VIEWS]
        scene = data.get('scene') if isinstance(data.get('scene'), dict) else {}
        drafts['scene'] = {'preset': str(scene.get('preset') or ''), 'objects': [
            {'kind': str(item.get('kind') or ''), 'label': str(item.get('label') or '').strip()[:LABEL_MAX],
             'note': str(item.get('note') or '').strip()[:TEXT_MAX], 'evidenceIds': _ids(item.get('evidenceIds'))}
            for item in (scene.get('objects') or [])[:MAX_SCENE_OBJECTS] if isinstance(item, dict)]}
        return drafts


def default_writer():
    return ToklensWriter() if os.environ.get('SHIJI_VISION_KEY_FILE') else None
