from django.core.management import call_command
from django.test import TestCase

from crawler.services.classifier import (
    Lexicon, classify, compile_term, detect_language, detect_region, mentions_tanzania,
)


class TermMatchingTests(TestCase):
    def test_wildcard_matches_swahili_word_forms(self):
        pattern = compile_term('*lalamik*')
        for word in ('wanalalamika', 'walalamika', 'wamelalamikia'):
            self.assertTrue(pattern.search(word), word)

    def test_plain_term_matches_only_whole_words(self):
        self.assertFalse(compile_term('tax').search('taxi drivers'))
        self.assertTrue(compile_term('tax').search('a new tax on fuel'))

    def test_phrase_tolerates_line_breaks(self):
        self.assertTrue(compile_term('lack of').search('a lack\nof water'))


class RegionAndLanguageTests(TestCase):
    def test_everyday_swahili_word_is_not_a_region(self):
        self.assertEqual(detect_region('Mara nyingi wananchi hulalamika'), '')

    def test_marked_place_name_is_a_region(self):
        self.assertEqual(detect_region('Wakazi wa mkoani Mara wanalalamika'), 'Mara')

    def test_most_mentioned_region_wins(self):
        self.assertEqual(detect_region('Mbeya, then Arusha, then Arusha again'), 'Arusha')

    def test_language(self):
        self.assertEqual(detect_language('Wananchi wa kijiji hicho wamesema kuwa barabara ni mbovu sana'), 'sw')
        self.assertEqual(detect_language('The residents said that the road was in a poor state'), 'en')

    def test_tanzania_relevance(self):
        self.assertTrue(mentions_tanzania('Farmers in Tanzania protest', ''))
        self.assertFalse(mentions_tanzania('Kenyan farmers protest', 'Near the Tanzania border.'))


class ClassifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_crawler', verbosity=0, stdout=open('/dev/null', 'w'))

    def setUp(self):
        self.lexicon = Lexicon.load()

    def test_english_complaint(self):
        result = classify(
            'Mbeya residents complain over power outages',
            'Residents of Mbeya say outages have lasted days. Traders complain that electricity cuts ruin stock.',
            self.lexicon,
        )
        self.assertTrue(result.is_concern)
        self.assertEqual(result.concern_type, 'complaint')
        self.assertIn('energy', result.sector_scores)
        self.assertEqual(result.region, 'Mbeya')

    def test_swahili_challenge(self):
        result = classify(
            'Uhaba wa maji wawatesa wananchi Dodoma',
            'Wananchi wanasema uhaba wa maji safi umeendelea kwa miezi. Wakazi wanakabiliwa na ukosefu wa maji.',
            self.lexicon,
        )
        self.assertTrue(result.is_concern)
        self.assertEqual(result.concern_type, 'challenge')
        self.assertIn('water', result.sector_scores)
        self.assertEqual(result.language, 'sw')

    def test_swahili_appeal_in_the_headline_is_a_concern(self):
        result = classify('Wananchi waomba barabara ijengwe', 'Wananchi wa kijiji wamesema barabara ni mbovu.', self.lexicon)
        self.assertTrue(result.is_concern)
        self.assertIn('infrastructure', result.sector_scores)

    def test_first_person_forum_complaint_is_a_concern(self):
        for title in ('Wanafunzi hatujapata mkopo mwaka huu', 'Uchafu umezagaa mitaani, wahusika hamuoni'):
            result = classify(title, 'Hali hii imekuwa ya muda mrefu sasa.', self.lexicon)
            self.assertTrue(result.is_concern, title)
            self.assertEqual(result.concern_type, 'complaint')

    def test_official_visit_mentioning_challenges_is_not_a_concern(self):
        result = classify(
            'Minister meets envoy to discuss energy investment',
            'The minister said the talks addressed challenges in the energy sector. '
            'The delegation thanked the ministry for hosting the meeting.',
            self.lexicon,
        )
        self.assertFalse(result.is_concern)

    def test_challenges_as_a_verb_in_the_headline_is_not_a_concern(self):
        result = classify(
            'Minister challenges leaders to uphold accountability',
            'The minister challenges leaders to serve. The challenges of leadership are many. Challenges remain.',
            self.lexicon,
        )
        self.assertFalse(result.is_concern)

    def test_routine_news_is_not_a_concern(self):
        result = classify(
            'Bank opens new branch in Arusha',
            'The bank opened a branch on Monday and invited customers to the event.',
            self.lexicon,
        )
        self.assertFalse(result.is_concern)
        self.assertEqual(result.concern_type, '')
