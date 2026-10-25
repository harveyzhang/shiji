import unittest

from shiji.contract import validate_case
from shiji.investigate import SLOTS, investigate


def page(title, site, passage='', n=0):
    return {'title': title, 'url': f'https://{site}/{abs(hash((title, passage, n)))}', 'site': site, 'passage': passage}


class FakeSearch:
    """Answers by query keyword; records what was asked; can fail on chosen queries."""

    def __init__(self, answers=None, fail_on=()):
        self.answers, self.fail_on, self.queries = answers or {}, fail_on, []

    def __call__(self, query, **params):
        self.queries.append(query)
        if any(word in query for word in self.fail_on):
            raise RuntimeError('元宝搜索请求失败（HTTP 500）。')
        for keyword, pages in self.answers.items():
            if keyword in query:
                return list(pages)
        return []


LAMP = FakeSearch({
    '历史': [page('煤油灯的历史', 'a.com', '煤油灯在清末传入，民国时期普及'), page('老物件：煤油灯', 'b.com', '清代晚期的煤油灯')],
    '场景': [page('当年家里怎么用煤油灯', 'c.com', '煤油灯放在桌上照明')],
    '煤油灯': [page('煤油灯', 'a.com', '煤油灯是一种照明工具'), page('煤油灯百科', 'b.com', '煤油灯')],
})
STATION = FakeSearch({
    '历史': [page('老火车站的历史', 'd.com', '民国时期的火车站候车室'), page('铁路记忆', 'e.com', '民国火车站')],
    '环境': [page('候车室当年的布局', 'f.com', '火车站候车室有长凳和站牌')],
    '火车站': [page('老火车站 老照片', 'd.com', '火车站 候车室'), page('火车站旧影', 'e.com', '火车站')],
})


def writer(**parts):
    """A fake writer: returns fixed drafts and records which sources it was shown."""
    def write(term, input_type, observations, sources):
        write.seen.append(sources)
        drafts = {'identity': {'text': '', 'evidenceIds': []}, 'era': {'text': '', 'evidenceIds': [], 'conflict': False},
                  'context': {'text': '', 'evidenceIds': []}}
        drafts.update(parts)
        return drafts
    write.seen = []
    return write


class WriterIntegrationTests(unittest.TestCase):
    """The model words the claims; the code decides how strong they are."""

    def test_writer_words_replace_rule_claims_when_citations_hold(self):
        case = investigate('object', '煤油灯', LAMP, write=writer(
            era={'text': '资料说清末传入、民国普及', 'evidenceIds': ['src-3', 'src-4'], 'conflict': False}))
        era = case['claims'][1]
        self.assertEqual((era['text'], era['status']), ('资料说清末传入、民国普及', 'supported'))

    def test_one_cited_publisher_is_only_inferred(self):
        case = investigate('object', '煤油灯', LAMP, write=writer(context={'text': '当年放在桌上照明', 'evidenceIds': ['src-5']}))
        self.assertEqual((case['claims'][2]['text'], case['claims'][2]['status']), ('当年放在桌上照明', 'inferred'))

    def test_writer_cannot_weaken_a_better_supported_rule_claim(self):
        rule = investigate('object', '煤油灯', LAMP)['claims'][0]
        case = investigate('object', '煤油灯', LAMP, write=writer(identity={'text': '这是一盏煤油灯', 'evidenceIds': ['src-1']}))
        self.assertEqual((rule['status'], case['claims'][0]), ('supported', rule))

    def test_strength_is_compared_by_status_not_by_how_many_sources_a_rule_listed(self):
        busy = FakeSearch({'场景': [page('煤油灯照明', 'c.com', '煤油灯'), page('煤油灯记忆', 'd.com', '煤油灯'),
                                    page('煤油灯往事', 'e.com', '煤油灯')],
                           '煤油灯': [page('煤油灯', 'a.com', '煤油灯'), page('煤油灯百科', 'b.com', '煤油灯')]})
        context_ids = investigate('object', '煤油灯', busy)['searches'][2]['sourceIds']
        case = investigate('object', '煤油灯', busy, write=writer(
            context={'text': '当年主要用于夜间照明', 'evidenceIds': context_ids[:2]}))
        self.assertEqual((case['claims'][2]['text'], case['claims'][2]['status']), ('当年主要用于夜间照明', 'supported'))

    def test_made_up_citations_fall_back_to_the_rule_claim(self):
        rule = investigate('object', '煤油灯', LAMP)['claims'][0]
        case = investigate('object', '煤油灯', LAMP, write=writer(identity={'text': '这是一盏煤油灯', 'evidenceIds': ['src-99']}))
        self.assertEqual(case['claims'][0], rule)

    def test_identity_citations_must_mention_the_term(self):
        search = FakeSearch({'煤油灯': [page('煤油灯', 'a.com', '煤油灯'), page('煤油灯百科', 'b.com', '煤油灯'),
                                        page('天气预报', 'x.com', '晴')]})
        case = investigate('object', '煤油灯', search, write=writer(identity={'text': '这是一盏煤油灯', 'evidenceIds': ['src-3']}))
        self.assertNotIn('src-3', case['claims'][0]['evidenceIds'])

    def test_conflict_needs_two_publishers(self):
        case = investigate('object', '煤油灯', LAMP, write=writer(
            era={'text': '资料里的年代说法不一', 'evidenceIds': ['src-3', 'src-4'], 'conflict': True}))
        self.assertEqual(case['claims'][1]['status'], 'conflicting')

    def test_writer_failure_keeps_rule_claims_with_a_warning(self):
        def broken(*args):
            raise RuntimeError('归纳请求失败（HTTP 429）。')
        rule = investigate('object', '煤油灯', LAMP)['claims']
        case = investigate('object', '煤油灯', LAMP, write=broken)
        self.assertEqual(case['claims'], rule)
        self.assertTrue(any('归纳失败' in w and 'HTTP 429' in w for w in case['warnings']))

    def test_writer_sees_only_this_cases_sources_and_empty_drafts_change_nothing(self):
        write = writer()
        case = investigate('object', '煤油灯', LAMP, write=write)
        shown = write.seen[0]
        self.assertEqual({s['id'] for s in shown}, {s['id'] for s in case['sources']})
        self.assertEqual({s['purpose'] for s in shown}, {'identify', 'verify', 'context'})
        self.assertEqual(case['claims'], investigate('object', '煤油灯', LAMP)['claims'])

    def test_political_writer_text_is_not_used(self):
        case = investigate('object', '煤油灯', LAMP, write=writer(
            era={'text': '总书记说过煤油灯', 'evidenceIds': ['src-3', 'src-4'], 'conflict': False}))
        self.assertNotIn('总书记', case['claims'][1]['text'])

    def test_writer_is_not_asked_when_search_failed(self):
        write = writer()
        investigate('object', '煤油灯', FakeSearch(fail_on=('煤油灯',)), write=write)
        self.assertEqual(write.seen, [])


class WriterSceneTests(unittest.TestCase):
    """The model picks the scene from the whitelist; the code places it and decides what counts as evidence."""

    def suggest(self, search=LAMP, **scene):
        return investigate('object', '煤油灯', search, write=writer(scene=scene))['scene']

    def test_writer_picks_the_preset_and_components(self):
        scene = self.suggest(preset='street_station', objects=[
            {'kind': 'bench', 'label': '候车长椅', 'note': '资料提到候车室有长椅', 'evidenceIds': ['src-5']},
            {'kind': 'spaceship', 'label': '飞船', 'note': '', 'evidenceIds': []},
            {'kind': 'signboard', 'label': '站牌', 'note': '没有出处的说法', 'evidenceIds': []}])
        kinds = {o['kind']: o for o in scene['objects']}
        self.assertEqual(scene['preset'], 'street_station')
        self.assertNotIn('spaceship', kinds)
        self.assertEqual((kinds['bench']['evidenceClass'], kinds['bench']['evidenceIds']), ('inferred', ['src-5']))
        self.assertEqual(kinds['signboard']['evidenceClass'], 'filler')
        self.assertNotIn('没有出处的说法', kinds['signboard']['note'])

    def test_scene_citations_must_exist(self):
        scene = self.suggest(preset='historic_room', objects=[{'kind': 'table', 'label': '方桌', 'note': '摆在桌上', 'evidenceIds': ['src-99']}])
        table = [o for o in scene['objects'] if o['kind'] == 'table'][0]
        self.assertEqual(table['evidenceClass'], 'filler')

    def test_small_things_stand_on_the_table(self):
        scene = self.suggest(preset='historic_room', objects=[
            {'kind': 'table', 'label': '方桌', 'note': '放在桌上用', 'evidenceIds': ['src-5']},
            {'kind': 'lamp', 'label': '煤油灯', 'note': '桌上照明', 'evidenceIds': ['src-5']}])
        table, lamp = ([o for o in scene['objects'] if o['kind'] == k][0] for k in ('table', 'lamp'))
        self.assertEqual((lamp['position'][0], lamp['position'][2]), (table['position'][0], table['position'][2]))
        self.assertGreater(lamp['position'][1], 0)

    def test_an_artifact_named_like_a_lamp_is_drawn_as_a_lamp(self):
        scene = self.suggest(preset='historic_room', objects=[{'kind': 'artifact', 'label': '煤油灯', 'note': '室内照明', 'evidenceIds': ['src-5']}])
        self.assertEqual([o['kind'] for o in scene['objects'] if o['label'] == '煤油灯'], ['lamp'])

    def test_indoors_a_table_is_set_out_for_things_that_stand_on_one(self):
        scene = self.suggest(preset='historic_room', objects=[{'kind': 'lamp', 'label': '煤油灯', 'note': '桌上照明', 'evidenceIds': ['src-5']}])
        tables = [o for o in scene['objects'] if o['kind'] == 'table']
        lamp = [o for o in scene['objects'] if o['kind'] == 'lamp'][0]
        self.assertEqual([t['evidenceClass'] for t in tables], ['filler'])
        self.assertEqual((lamp['position'][0], lamp['position'][2]), (tables[0]['position'][0], tables[0]['position'][2]))
        self.assertGreater(lamp['position'][1], 0)

    def test_two_things_on_one_table_do_not_overlap(self):
        scene = self.suggest(preset='historic_room', objects=[
            {'kind': 'table', 'label': '方桌', 'note': '', 'evidenceIds': []}, {'kind': 'lamp', 'label': '煤油灯', 'note': '', 'evidenceIds': []},
            {'kind': 'artifact', 'label': '茶壶', 'note': '', 'evidenceIds': []}])
        on_table = [tuple(o['position']) for o in scene['objects'] if o['position'][1] > 0]
        self.assertEqual(len(on_table), len(set(on_table)))

    def test_tall_things_stand_behind_short_ones(self):
        for preset, tall, short in (('street_station', 'door_window', 'suitcase'), ('historic_room', 'shelf', 'chair')):
            with self.subTest(preset=preset):
                scene = self.suggest(preset=preset, objects=[{'kind': tall, 'label': '', 'note': '', 'evidenceIds': []},
                                                             {'kind': short, 'label': '', 'note': '', 'evidenceIds': []}])
                z = {o['kind']: o['position'][2] for o in scene['objects']}
                self.assertLess(z[tall], z[short])

    def test_a_sparse_scene_is_filled_with_marked_filler(self):
        scene = self.suggest(preset='street_station', objects=[{'kind': 'signboard', 'label': '站名', 'note': '资料提到站名', 'evidenceIds': ['src-5']}])
        props = [o for o in scene['objects'] if o['kind'] not in ('floor', 'wall') and o['id'] != 'obj-core']
        self.assertGreaterEqual(len(props), 3)
        self.assertTrue(all(o['evidenceClass'] == 'filler' and not o['evidenceIds'] for o in props if o['label'] != '站名'))

    def test_writer_slots_keep_the_lane_in_front_of_the_core_clear(self):
        for preset, slots in SLOTS.items():  # The writer may pick tall kinds, so nothing may stand between the viewer and the core.
            for x, _, z in slots:
                with self.subTest(preset=preset, slot=(x, z)):
                    self.assertTrue(abs(x) >= 1.4 or z <= -0.5)

    def test_unknown_identity_keeps_the_neutral_desk(self):
        nothing = FakeSearch({'铜件': [page('无关页面', 'x.com', '天气预报')]})
        scene = investigate('object', '奇怪的铜件', nothing, write=writer(scene={'preset': 'historic_room', 'objects': []}))['scene']
        self.assertEqual(scene['preset'], 'neutral_desk')

    def test_without_a_usable_suggestion_the_rule_scene_stays(self):
        self.assertEqual(self.suggest(preset='castle', objects=[]), investigate('object', '煤油灯', LAMP)['scene'])


class SourceTypeTests(unittest.TestCase):
    """Judges should see at a glance whether a source is an archive, a government page, the press or just a post."""

    def test_sources_are_labelled_by_kind(self):
        pages = [{'title': '煤油灯', 'url': 'https://www.muping.gov.cn/a', 'site': '烟台市牟平区政务网', 'passage': '煤油灯'},
                 {'title': '平江路', 'url': 'https://dfzb.suzhou.gov.cn/b', 'site': '苏州市地方志工作办公室', 'passage': '煤油灯'},
                 {'title': '远去的煤油灯', 'url': 'https://www.peopleapp.com/c', 'site': '人民日报', 'passage': '煤油灯'},
                 {'title': '煤油灯', 'url': 'https://baike.baidu.com/d', 'site': '百度百科', 'passage': '煤油灯'},
                 {'title': '我家的煤油灯', 'url': 'https://www.toutiao.com/e', 'site': '今日头条', 'passage': '煤油灯'}]
        case = investigate('object', '煤油灯', FakeSearch({'历史': pages[3:], '煤油灯': pages[:3]}))  # 4 pages per search at most
        types = {s['site']: s['type'] for s in case['sources']}
        self.assertEqual(types, {'烟台市牟平区政务网': 'government', '苏州市地方志工作办公室': 'archive', '人民日报': 'media',
                                 '百度百科': 'encyclopedia', '今日头条': 'other'})
        self.assertEqual(validate_case(case), [])


class ProgressTests(unittest.TestCase):
    """The waiting screen shows what the investigation is really doing, stage by stage."""

    def test_each_stage_is_reported_in_order(self):
        events = []
        observe = lambda image, kind: {'observations': ['玻璃灯罩'], 'term': '煤油灯'}
        investigate('object', '', FakeSearch(LAMP.answers), image=b'jpeg', observe=observe, write=writer(),
                    progress=lambda event, data: events.append(event))
        self.assertEqual(events, ['observe', 'observed', 'search', 'searched', 'search', 'searched',
                                  'search', 'searched', 'write', 'scene'])

    def test_searched_events_carry_the_real_query_and_count(self):
        seen = []
        investigate('object', '煤油灯', FakeSearch(LAMP.answers), progress=lambda event, data: seen.append((event, data)))
        searched = [data for event, data in seen if event == 'searched']
        self.assertEqual([d['query'] for d in searched], ['煤油灯', '煤油灯 历史 年代', '煤油灯 当年 使用 场景'])
        self.assertTrue(all(d['ok'] and d['count'] >= 1 for d in searched))

    def test_a_failed_vision_step_is_reported_instead_of_left_spinning(self):
        def broken(image, kind):
            raise RuntimeError('识图请求失败（HTTP 500）。')
        seen = []
        investigate('object', '煤油灯', FakeSearch(LAMP.answers), image=b'jpeg', observe=broken,
                    progress=lambda event, data: seen.append((event, data)))
        self.assertEqual([data.get('failed') for event, data in seen if event == 'observed'], [True])

    def test_every_search_event_says_why_it_was_made(self):
        seen = []
        investigate('object', '煤油灯', FakeSearch(LAMP.answers), progress=lambda event, data: seen.append((event, data)))
        self.assertTrue(all(data.get('reason') for event, data in seen if event == 'search'))

    def test_a_broken_listener_never_breaks_the_case(self):
        def gone(event, data):
            raise RuntimeError('listener gone')
        self.assertEqual(investigate('object', '煤油灯', FakeSearch(LAMP.answers), progress=gone)['status'], 'text_only')


class SearchChainTests(unittest.TestCase):
    """The second and later searches are planned from what the first ones found, and each says why."""

    def test_a_question_in_the_clue_box_is_not_searched_as_a_name(self):
        search = FakeSearch(STATION.answers)
        investigate('old_photo', '这可能是什么场景？它当年如何使用？', search, image=b'jpeg',
                    observe=lambda image, kind: {'observations': ['候车室里有长椅'], 'term': '火车站'})
        self.assertTrue(search.queries)
        self.assertTrue(all(q.startswith('火车站') for q in search.queries))

    def test_a_year_in_the_clue_is_checked_instead_of_searched_as_part_of_the_name(self):
        search = FakeSearch(STATION.answers)
        case = investigate('old_photo', '1906年的火车站', search)
        self.assertEqual(search.queries[0], '火车站 老照片')
        verify = [s for s in case['searches'] if s['purpose'] == 'verify'][0]
        self.assertEqual(verify['query'], '火车站 1906年')
        self.assertIn('1906年', verify['reason'])

    def test_a_generic_clue_is_not_a_name_but_its_period_is_checked(self):
        search = FakeSearch({'清代女子服饰': [page('清代女子服饰', 'a.com', '清代女子服饰')]})
        case = investigate('old_photo', '清朝的老照片', search, image=b'jpeg',
                           observe=lambda image, kind: {'observations': ['两位女子身着刺绣服饰'], 'term': '清代女子服饰'})
        self.assertEqual(search.queries[0], '清代女子服饰 老照片')
        verify = [s for s in case['searches'] if s['purpose'] == 'verify'][0]
        self.assertEqual(verify['query'], '清代女子服饰 清代')

    def test_without_a_usable_name_nothing_is_searched(self):
        search = FakeSearch(STATION.answers)
        case = investigate('old_photo', '老照片', search)
        self.assertEqual((search.queries, case['status']), ([], 'insufficient'))

    def test_the_check_search_targets_the_year_most_publishers_mention(self):
        search = FakeSearch({'老照片': [page('东站旧影', 'a.com', '东站1906年建成'), page('东站', 'b.com', '东站于1906年启用'),
                                        page('东站', 'c.com', '1901年始建的东站')]})
        case = investigate('old_photo', '东站', search)
        verify = [s for s in case['searches'] if s['purpose'] == 'verify'][0]
        self.assertEqual(verify['query'], '东站 1906年')
        self.assertIn('2 个发布方', verify['reason'])
        self.assertNotIn('crosscheck', [s['purpose'] for s in case['searches']])

    def test_two_well_supported_years_trigger_one_more_search_to_tell_them_apart(self):
        search = FakeSearch({'老照片': [page('东站', 'a.com', '东站1901年始建'), page('东站', 'b.com', '东站1901年始建，1906年建成'),
                                        page('东站', 'c.com', '东站1906年建成')]})
        case = investigate('old_photo', '东站', search)
        cross = [s for s in case['searches'] if s['purpose'] == 'crosscheck']
        self.assertEqual([s['query'] for s in cross], ['东站 1901年 1906年'])
        self.assertEqual(validate_case(case), [])

    def test_every_search_records_why_it_was_made(self):
        case = investigate('object', '煤油灯', LAMP)
        self.assertTrue(all(s.get('reason') for s in case['searches']))


class CrossCheckTests(unittest.TestCase):
    """What the report needs to show who agrees, who disagrees and what to check next."""

    def test_sources_name_their_publisher_and_claims_say_what_to_check_next(self):
        case = investigate('object', '煤油灯', LAMP)
        self.assertTrue(all(s['publisher'] for s in case['sources']))
        self.assertTrue(all(c['nextStep'] for c in case['claims']))

    def test_the_writer_splits_a_disputed_era_into_views_with_real_sources(self):
        era = {'text': '建成年份说法不一', 'evidenceIds': ['src-1', 'src-2', 'src-3', 'src-4'], 'conflict': True,
               'views': [{'text': '1906年建成', 'evidenceIds': ['src-1', 'src-4']}, {'text': '1903年建成', 'evidenceIds': ['src-2', 'src-99']}]}
        case = investigate('object', '煤油灯', LAMP, write=writer(era=era))
        claim = case['claims'][1]
        self.assertEqual(claim['status'], 'conflicting')
        self.assertEqual(claim['views'], [{'text': '1906年建成', 'evidenceIds': ['src-1', 'src-4']},
                                          {'text': '1903年建成', 'evidenceIds': ['src-2']}])
        self.assertEqual(validate_case(case), [])

    def test_undisputed_claims_carry_no_views(self):
        case = investigate('object', '煤油灯', LAMP)
        self.assertEqual([c['views'] for c in case['claims']], [[], [], []])


class InvestigateTests(unittest.TestCase):
    def test_text_only_object_runs_three_searches_with_different_purposes(self):
        case = investigate('object', '煤油灯', LAMP)
        self.assertEqual([s['purpose'] for s in case['searches']], ['identify', 'verify', 'context'])
        self.assertEqual(len({s['query'] for s in case['searches']}), 3)
        self.assertEqual(case['status'], 'text_only')
        self.assertTrue(any('文字线索' in w for w in case['warnings']))
        self.assertEqual(validate_case(case), [])

    def test_identity_is_supported_only_when_two_different_sites_agree(self):
        case = investigate('object', '煤油灯', LAMP)
        identity = case['claims'][0]
        self.assertEqual(identity['status'], 'supported')
        sites = {s['site'] for s in case['sources'] if s['id'] in identity['evidenceIds']}
        self.assertGreaterEqual(len(sites), 2)

    def test_two_pages_from_one_site_are_not_independent_evidence(self):
        same_site = FakeSearch({'煤油灯': [page('煤油灯', 'a.com', '煤油灯', 1), page('煤油灯 图片', 'a.com', '煤油灯', 2)]})
        identity = investigate('object', '煤油灯', same_site)['claims'][0]
        self.assertEqual(identity['status'], 'inferred')

    def test_wikipedia_mirrors_count_as_one_source(self):
        mirrors = FakeSearch({'候车室': [
            {'title': '火车站候车室 (绥芬河)', 'url': 'http://zh.wikipedia-on-ipfs.org/wiki/1', 'site': 'zh.wikipedia-on-ipfs.org', 'passage': '候车室'},
            {'title': '中东铁路记忆馆 - 维基百科,自由的百科全书', 'url': 'https://5qqdr.feinidi.com/wiki/2', 'site': '5qqdr.feinidi.com', 'passage': '候车室'},
            {'title': '火車站候車室 (綏芬河)', 'url': 'https://factpedia.org/wiki/3', 'site': 'factpedia.org', 'passage': '候车室'}]})
        identity = investigate('old_photo', '候车室', mirrors)['claims'][0]
        self.assertEqual(identity['status'], 'inferred')

    def test_subdomains_of_one_site_count_once(self):
        one_site = FakeSearch({'煤油灯': [
            {'title': '煤油灯', 'url': 'https://m.gmw.cn/1', 'site': '光明网手机版', 'passage': '煤油灯'},
            {'title': '煤油灯', 'url': 'https://epaper.gmw.cn/2', 'site': '光明日报电子版', 'passage': '煤油灯'}]})
        self.assertEqual(investigate('object', '煤油灯', one_site)['claims'][0]['status'], 'inferred')

    def test_two_local_governments_are_two_sources(self):
        two_cities = FakeSearch({'煤油灯': [
            {'title': '煤油灯', 'url': 'https://www.muping.gov.cn/1', 'site': '烟台市牟平区政务网', 'passage': '煤油灯'},
            {'title': '煤油灯的故事', 'url': 'http://www.kenli.gov.cn/2', 'site': '垦利区电子政务', 'passage': '煤油灯'}]})
        self.assertEqual(investigate('object', '煤油灯', two_cities)['claims'][0]['status'], 'supported')

    def test_nothing_relevant_means_insufficient_and_a_neutral_desk(self):
        case = investigate('object', '奇怪的铜件', FakeSearch({'铜件': [page('无关页面', 'x.com', '天气预报')]}))
        self.assertEqual(case['status'], 'insufficient')
        self.assertEqual(case['scene']['preset'], 'neutral_desk')
        self.assertNotIn('supported', [c['status'] for c in case['claims']])
        self.assertEqual(validate_case(case), [])

    def test_unknown_identity_leaves_era_and_context_open(self):
        search = FakeSearch({'历史': [page('钟楼的历史', 'a.com', '清末建成'), page('老钟楼', 'b.com', '清末民初')],
                             '环境': [page('钟楼集市', 'c.com', '拱顶钟楼市场 当年是集市'), page('集市', 'd.com', '拱顶钟楼市场')]})
        case = investigate('old_photo', '拱顶钟楼市场', search)
        self.assertEqual([c['status'] for c in case['claims']], ['unknown', 'unknown', 'unknown'])
        self.assertEqual([c['evidenceIds'] for c in case['claims']], [[], [], []])
        self.assertEqual(validate_case(case), [])

    def test_a_city_prefix_matches_sources_naming_both_the_city_and_the_place(self):
        qianmen = FakeSearch({'北京前门火车站': [page('趣说北京：前门火车站', 'a.com', '北京前门大街东南角的前门火车站'),
                                              page('前门火车站旧影', 'b.com', '北京正阳门东侧的前门火车站')]})
        self.assertEqual(investigate('old_photo', '北京前门火车站', qianmen)['claims'][0]['status'], 'supported')

    def test_a_period_name_matches_sources_naming_the_period_and_the_thing(self):
        qing = FakeSearch({'清代女子服饰': [page('晚清：清朝女子的发型和衣着', 'a.com', '清朝女子的发型和衣着'),
                                           page('清朝闺秀服饰文物展出', 'b.com', '30件清朝闺秀服饰文物')]})
        self.assertEqual(investigate('old_photo', '清代女子服饰', qing)['claims'][0]['status'], 'supported')

    def test_naming_only_the_period_is_not_enough(self):
        other = FakeSearch({'清代女子服饰': [page('清朝的科举', 'a.com', '清朝科举制度'), page('清代官制', 'b.com', '清代官制')]})
        self.assertEqual(investigate('old_photo', '清代女子服饰', other)['claims'][0]['status'], 'unknown')

    def test_dropping_the_city_prefix_still_needs_the_city_in_the_source(self):
        elsewhere = FakeSearch({'北京前门火车站': [page('前门火车站', 'a.com', '某地的前门火车站'), page('前门火车站', 'b.com', '前门火车站')]})
        self.assertEqual(investigate('old_photo', '北京前门火车站', elsewhere)['claims'][0]['status'], 'unknown')

    def test_failed_searches_are_reported_without_pretending_results(self):
        case = investigate('object', '煤油灯', FakeSearch(fail_on=('煤油灯',)))
        self.assertEqual(case['status'], 'search_failed')
        self.assertEqual([s['ok'] for s in case['searches']], [False, False, False])
        self.assertEqual(case['sources'], [])
        self.assertEqual(validate_case(case), [])

    def test_one_failed_search_is_flagged_and_the_rest_still_count(self):
        case = investigate('object', '煤油灯', FakeSearch(LAMP.answers, fail_on=('场景',)))
        self.assertEqual([s['ok'] for s in case['searches']], [True, True, False])
        self.assertTrue(any('失败' in w for w in case['warnings']))

    def test_search_switched_off_reaches_no_conclusion(self):
        case = investigate('object', '煤油灯', None)
        self.assertEqual(case['status'], 'search_failed')
        self.assertTrue(any('搜索已关闭' in w for w in case['warnings']))
        self.assertEqual(validate_case(case), [])

    def test_identity_and_politics_questions_are_refused_before_searching(self):
        search = FakeSearch()
        case = investigate('old_photo', '照片里这个人是谁', search)
        self.assertEqual((case['status'], search.queries), ('refused', []))
        self.assertEqual(validate_case(case), [])

    def test_political_sources_are_dropped_with_a_warning(self):
        search = FakeSearch({'粮票': [page('粮票的记忆', 'a.com', '粮票是计划经济的见证'),
                                      page('讲话摘编', 'b.com', '总书记在讲话中指出粮票')]})
        case = investigate('object', '粮票', search)
        self.assertNotIn('讲话摘编', [s['title'] for s in case['sources']])
        self.assertTrue(any('时政' in w for w in case['warnings']))

    def test_dead_links_are_dropped_before_they_become_evidence(self):
        case = investigate('object', '煤油灯', LAMP, check_link=lambda url: 'a.com' not in url)
        self.assertNotIn('a.com', {s['site'] for s in case['sources']})

    def test_era_claim_flags_disagreeing_sources_as_conflicting(self):
        search = FakeSearch({'历史': [page('一', 'a.com', '清代'), page('二', 'b.com', '清代'),
                                      page('三', 'c.com', '民国'), page('四', 'd.com', '民国')],
                             '铜壶': [page('铜壶', 'a.com', '铜壶')]})
        era = investigate('object', '铜壶', search)['claims'][1]
        self.assertEqual(era['status'], 'conflicting')

    def test_scene_follows_the_evidence_not_a_fixed_template(self):
        lamp = investigate('object', '煤油灯', LAMP)['scene']
        station = investigate('old_photo', '老火车站 候车室', STATION)['scene']
        self.assertNotEqual(lamp['preset'], station['preset'])
        self.assertNotEqual({o['kind'] for o in lamp['objects']}, {o['kind'] for o in station['objects']})
        self.assertIn('lamp', {o['kind'] for o in lamp['objects']})

    def test_scene_objects_cite_the_context_search_when_it_found_something(self):
        case = investigate('object', '煤油灯', LAMP)
        context_ids = set(case['searches'][2]['sourceIds'])
        cited = [o for o in case['scene']['objects'] if o['evidenceClass'] == 'inferred']
        self.assertTrue(cited and all(set(o['evidenceIds']) <= context_ids for o in cited))

    def test_image_observations_feed_the_search_term(self):
        observe = lambda image, input_type: {'observations': ['玻璃灯罩', '金属底座'], 'term': '煤油灯'}
        search = FakeSearch(LAMP.answers)
        case = investigate('object', '', search, image=b'jpeg', observe=observe)
        self.assertEqual([o['source'] for o in case['observations']], ['image', 'image'])
        self.assertIn('煤油灯', search.queries[0])
        self.assertEqual(case['status'], 'ok')

    def test_observer_failure_reason_is_shown(self):
        def limited(image, input_type):
            raise RuntimeError('识图请求失败（HTTP 429）。')
        case = investigate('object', '煤油灯', LAMP, image=b'jpeg', observe=limited)
        self.assertTrue(any('HTTP 429' in w for w in case['warnings']))

    def test_observer_failure_falls_back_to_the_text_hint(self):
        def broken(image, input_type):
            raise RuntimeError('vision down')
        case = investigate('object', '煤油灯', LAMP, image=b'jpeg', observe=broken)
        self.assertEqual(case['status'], 'text_only')
        self.assertTrue(any('识图失败' in w for w in case['warnings']))


if __name__ == '__main__':
    unittest.main()
