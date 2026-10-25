import unittest

from shiji.contract import CAPTION, normalize_scene, validate_case


def obj(**changes):
    base = {'id': 'obj-1', 'kind': 'table', 'label': '桌子', 'note': '说明', 'position': [0, 0, 0], 'scale': 1,
            'rotationY': 0, 'evidenceClass': 'filler', 'evidenceIds': [], 'hotspot': False}
    base.update(changes)
    return base


class NormalizeSceneTests(unittest.TestCase):
    def normalize(self, scene, sources=('src-1',)):
        warnings = []
        return normalize_scene(scene, set(sources), warnings), warnings

    def test_unknown_preset_falls_back_to_the_neutral_desk_with_a_warning(self):
        scene, warnings = self.normalize({'preset': 'castle', 'objects': [obj(), obj(id='obj-2')]})
        self.assertEqual(scene['preset'], 'neutral_desk')
        self.assertTrue(any('castle' in w for w in warnings))

    def test_unknown_kind_is_rendered_as_a_generic_artifact(self):
        scene, _ = self.normalize({'preset': 'historic_room', 'objects': [obj(kind='spaceship'), obj(id='obj-2')]})
        self.assertEqual(scene['objects'][0]['kind'], 'artifact')

    def test_position_scale_and_rotation_are_clamped_into_the_stage(self):
        scene, _ = self.normalize({'preset': 'historic_room',
                                   'objects': [obj(position=[9, -1, -9], scale=5, rotationY=450), obj(id='obj-2')]})
        first = scene['objects'][0]
        self.assertEqual((first['position'], first['scale'], first['rotationY']), ([3, 0, -3], 2, 90))

    def test_supported_object_without_a_real_source_is_downgraded_to_inferred(self):
        scene, warnings = self.normalize({'preset': 'historic_room',
                                          'objects': [obj(evidenceClass='supported', evidenceIds=['src-9']), obj(id='obj-2')]})
        first = scene['objects'][0]
        self.assertEqual((first['evidenceClass'], first['evidenceIds']), ('inferred', []))
        self.assertTrue(warnings)

    def test_scene_keeps_at_most_eight_objects_and_at_least_two_hotspots(self):
        scene, _ = self.normalize({'preset': 'historic_room', 'objects': [obj(id=f'obj-{i}') for i in range(12)]})
        self.assertEqual(len(scene['objects']), 8)
        self.assertGreaterEqual(sum(o['hotspot'] for o in scene['objects']), 2)

    def test_floor_and_wall_are_never_made_clickable(self):
        scene, _ = self.normalize({'preset': 'historic_room', 'objects': [
            obj(id='obj-floor', kind='floor'), obj(id='obj-wall', kind='wall'),
            obj(id='obj-core', kind='display_stand', hotspot=True), obj(id='obj-desk')]})
        self.assertEqual({o['id'] for o in scene['objects'] if o['hotspot']}, {'obj-core', 'obj-desk'})

    def test_caption_always_says_the_scene_is_exploratory(self):
        scene, _ = self.normalize({'preset': 'historic_room', 'caption': '真实复原', 'objects': [obj(), obj(id='obj-2')]})
        self.assertEqual(scene['caption'], CAPTION)


class ValidateCaseTests(unittest.TestCase):
    def case(self, **changes):
        base = {'caseId': 'c1', 'inputType': 'object', 'status': 'ok', 'warnings': [],
                'observations': [{'text': '玻璃灯罩', 'source': 'image', 'confirmed': False}],
                'searches': [{'purpose': 'identify', 'query': '煤油灯', 'ok': True, 'sourceIds': ['src-1']}],
                'sources': [{'id': 'src-1', 'title': 't', 'url': 'https://e.com/1', 'site': 'e.com', 'publishedAt': None, 'snippet': 's'}],
                'claims': [{'id': 'claim-1', 'text': 'x', 'status': 'supported', 'evidenceIds': ['src-1']}],
                'scene': {'preset': 'historic_room', 'caption': CAPTION,
                          'objects': [obj(hotspot=True), obj(id='obj-2', hotspot=True)]}}
        base.update(changes)
        return base

    def test_a_well_formed_case_has_no_errors(self):
        self.assertEqual(validate_case(self.case()), [])

    def test_bad_enums_and_dangling_evidence_are_reported(self):
        errors = ' '.join(validate_case(self.case(
            status='done', inputType='video',
            claims=[{'id': 'claim-1', 'text': 'x', 'status': 'proven', 'evidenceIds': ['src-7']}])))
        for word in ('done', 'video', 'proven', 'src-7'):
            self.assertIn(word, errors)

    def test_source_type_must_be_a_known_kind(self):
        errors = validate_case(self.case(sources=[{'id': 'src-1', 'title': 't', 'url': 'https://e.com/1', 'site': 'e.com',
                                                    'type': 'blog', 'publishedAt': None, 'snippet': 's'}]))
        self.assertTrue(any('blog' in e for e in errors))

    def test_sources_need_a_real_web_link(self):
        errors = validate_case(self.case(sources=[{'id': 'src-1', 'title': 't', 'url': 'javascript:alert(1)', 'site': '',
                                                    'publishedAt': None, 'snippet': ''}]))
        self.assertTrue(any('url' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
