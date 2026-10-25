import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from shiji.contract import validate_case

MOCKS = Path(__file__).resolve().parent.parent/'mock'


def load(name):
    return json.loads((MOCKS/name).read_text(encoding='utf-8'))


class MockTests(unittest.TestCase):
    """The front end builds against these files first; they must follow the contract and never pass as real results."""

    def test_both_mocks_follow_the_contract(self):
        for name in ('mock-object-lamp.json', 'mock-photo-station.json'):
            with self.subTest(name=name):
                self.assertEqual(validate_case(load(name)), [])

    def test_mocks_say_they_are_not_real_search_results(self):
        for name in ('mock-object-lamp.json', 'mock-photo-station.json'):
            case = load(name)
            with self.subTest(name=name):
                self.assertTrue(case['caseId'].startswith('mock-'))
                self.assertTrue(any('开发模拟' in w for w in case['warnings']))
                self.assertTrue(all(urlparse(s['url']).hostname.endswith('.invalid') for s in case['sources']))

    def test_lamp_mock_shows_all_three_evidence_classes(self):
        classes = {o['evidenceClass'] for o in load('mock-object-lamp.json')['scene']['objects']}
        self.assertEqual(classes, {'supported', 'inferred', 'filler'})

    def test_the_two_scenes_differ_in_at_least_two_ways(self):
        lamp, station = load('mock-object-lamp.json')['scene'], load('mock-photo-station.json')['scene']
        differences = [lamp['preset'] != station['preset'],
                       {o['kind'] for o in lamp['objects']} != {o['kind'] for o in station['objects']},
                       [o['position'] for o in lamp['objects']] != [o['position'] for o in station['objects']]]
        self.assertGreaterEqual(sum(differences), 2)


if __name__ == '__main__':
    unittest.main()
