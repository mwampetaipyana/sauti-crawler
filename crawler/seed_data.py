"""Starting sources and vocabulary, loaded by `manage.py seed_crawler` and editable in the admin."""
import shlex
from urllib.parse import urlencode

EXCLUDES_NEWS = '\n'.join([
    r'/sports?/', r'/michezo/', r'/kimataifa/', r'/world/', r'/entertainment/', r'/burudani/',
    r'/lifestyle/', r'/magazines?/', r'/obituar', r'/tag/', r'/author/', r'/category/',
])


JAMIIFORUMS_FIRST_POST_XPATH = '(//article[contains(@class, "message")]//div[contains(@class, "bbWrapper")])[1]'
# Articles keep text directly under <main>; live pages keep it in a list of separate posts.
BBC_BODY_XPATH = (
    '//main/div/p | //main/h2 | //main/div/h2'
    ' | //main//ol[@role="list"]/li/article//*[self::p or self::h2 or self::h3][not(ancestor::figure)]'
)
BBC_EXCLUDES = r'/bbc_swahili_radio/'  # Audio programmes with no story text.
BBC_REFRESH = r'/live/'
NATION_MEDIA_BODY_XPATH = '//*[contains(@class, "text-block") and contains(@class, "blk-txt")]'


def gdelt_url(query):
    params = {'query': query, 'mode': 'artlist', 'format': 'json', 'maxrecords': 75, 'sort': 'datedesc', 'timespan': '7d'}
    return f'https://api.gdeltproject.org/api/v2/doc/doc?{urlencode(params)}'


SOURCES = [
    {
        'name': 'The Citizen', 'kind': 'rss', 'url': 'https://www.thecitizen.co.tz/rss.xml', 'language': 'en',
        'enabled': False,
        'body_xpath': NATION_MEDIA_BODY_XPATH,
    },
    {
        'name': 'Mwananchi', 'kind': 'rss', 'url': 'https://www.mwananchi.co.tz/rss.xml', 'language': 'sw',
        'body_xpath': NATION_MEDIA_BODY_XPATH,
    },
    {'name': 'Daily News', 'kind': 'rss', 'url': 'https://dailynews.co.tz/feed/', 'language': 'en', 'enabled': False},
    {'name': 'HabariLeo', 'kind': 'rss', 'url': 'https://habarileo.co.tz/feed/', 'language': 'sw'},
    {'name': 'The Chanzo', 'kind': 'rss', 'url': 'https://thechanzo.com/feed/', 'language': ''},
    {'name': 'Jamhuri Media', 'kind': 'rss', 'url': 'https://www.jamhurimedia.co.tz/feed/', 'language': 'sw'},
    {'name': 'Nukta', 'kind': 'rss', 'url': 'https://nukta.co.tz/feed/', 'language': 'sw'},
    {
        'name': 'Sauti Kubwa', 'kind': 'rss', 'url': 'https://sautikubwa.org/feed/',
        'language': '', 'tanzania_focused': False,
    },
    {
        'name': 'Tanzania Times', 'kind': 'rss', 'url': 'https://www.tanzaniatimes.net/feed/',
        'language': 'en', 'tanzania_focused': False, 'enabled': False,
    },
    {
        'name': 'BBC Swahili', 'kind': 'rss', 'url': 'https://feeds.bbci.co.uk/swahili/rss.xml',
        'language': 'sw', 'tanzania_focused': False, 'body_xpath': BBC_BODY_XPATH,
        'refresh_pattern': BBC_REFRESH, 'exclude_patterns': f'{EXCLUDES_NEWS}\n{BBC_EXCLUDES}',
    },
    {
        'name': 'RFI Kiswahili', 'kind': 'rss', 'url': 'https://www.rfi.fr/sw/rss',
        'language': 'sw', 'tanzania_focused': False,
    },
    {
        'name': 'Web search: English coverage', 'kind': 'gdelt', 'tanzania_focused': False, 'enabled': False,
        'min_delay_seconds': 6.0, 'max_articles': 30,
        'url': gdelt_url(
            'Tanzania (residents OR villagers OR farmers OR shortage OR outage OR complain OR protest) '
            'sourcelang:english'
        ),
    },
    {
        'name': 'Web search: Swahili coverage', 'kind': 'gdelt', 'tanzania_focused': False,
        'min_delay_seconds': 6.0, 'max_articles': 30,
        'url': gdelt_url('(Tanzania OR wananchi OR malalamiko OR changamoto) sourcelang:swahili'),
    },
]

# Every board on jamiiforums.com/forums/, grouped as the site groups them.
# (name, path, enabled, tanzania_focused). Boards about other countries keep only posts that
# mention Tanzania. The English-only boards are off: with LANGUAGES = ['sw'] every post from them
# would be fetched and then discarded. Turn them on if English is allowed.
JAMIIFORUMS_BOARDS = [
    # General Forums
    ('JamiiCheck', 'jamiicheck.139', True, True),
    ('Siasa', 'jukwaa-la-siasa.6', True, True),
    ('Historia', 'jukwaa-la-historia.123', True, True),
    ('Habari na Hoja', 'habari-na-hoja-mchanganyiko.42', True, True),
    ('Jamii Intelligence', 'jamii-intelligence.51', True, True),
    ('Great Thinkers', 'great-thinkers.110', True, True),
    ('Kimataifa', 'international-forum.28', True, False),
    ('Biashara na Uchumi', 'jukwaa-la-biashara-na-uchumi.84', True, True),
    ('Kilimo na Ufugaji', 'ujasiriamali.114', True, True),
    ('Ujenzi na Makazi', 'jukwaa-la-ujenzi-na-makazi.135', True, True),
    ('Ajira na Tenda', 'nafasi-za-kazi-na-tenda.8', True, True),
    # JF English Only Forums
    ('News & Current Events', 'news-current-events.125', False, True),
    ('Tanzania Politics', 'tanzania-politics-discussion-board.128', False, True),
    ('General Discussions', 'general-discussions.126', False, True),
    # Africa Regional Specific Forums
    ('Kenya', 'kenyan-politics.115', True, False),
    ('Uganda', 'ugandan-news-and-politics.131', True, False),
    ('Rwanda', 'rwanda-forum.148', True, False),
    ('The Lounge', 'the-lounge.120', True, True),
    # JF Social Forums (Lounge)
    ('Chit-Chats', 'jf-chit-chats.103', True, True),
    ('Urembo na Mitindo', 'urembo-mitindo-na-utanashati-lifestyle.129', True, True),
    ('Jamii Photos', 'jamii-photos.92', True, True),
    ('Celebrities', 'celebrities-forum.53', True, True),
    ('Mahusiano', 'mahusiano-mapenzi-urafiki.62', True, True),
    ('Utambulisho', 'utambulisho-member-intro-forum.88', True, True),
    # Sports & Entertainment
    ('Michezo', 'jamii-sports.105', True, True),
    ('Burudani', 'entertainment.106', True, True),
    # Educational, Tech & Prof
    ('Afya', 'jamii-health-jukwaa-la-afya.61', True, True),
    ('Magari na Usafiri', 'jf-garage-magari-na-vyombo-vya-usafiri.130', True, True),
    ('Teknolojia na Sayansi', 'tech-gadgets-science-forum.20', True, True),
    ('Mapishi', 'jukwaa-la-mapishi.122', True, True),
    ('Sheria', 'jukwaa-la-sheria-the-legal-forum.87', True, True),
    ('Elimu', 'jukwaa-la-elimu-education-forum.11', True, True),
    ('Lugha', 'jukwaa-la-lugha.81', True, True),
]
JAMIIFORUMS_ENGLISH_BOARDS = {
    'news-current-events.125', 'tanzania-politics-discussion-board.128', 'general-discussions.126',
}
SOURCES += [
    {
        'name': f'JamiiForums: {name}', 'kind': 'rss', 'is_forum': True, 'enabled': enabled,
        'language': 'en' if path in JAMIIFORUMS_ENGLISH_BOARDS else 'sw', 'tanzania_focused': tanzania_focused,
        'url': f'https://www.jamiiforums.com/forums/{path}/index.rss',
        'body_xpath': JAMIIFORUMS_FIRST_POST_XPATH, 'max_articles': 10, 'min_delay_seconds': 5.0,
    }
    for name, path, enabled, tanzania_focused in JAMIIFORUMS_BOARDS
]
# Threads tagged "KERO" on any board: members' own complaints, the closest match to what this project
# looks for. Its feed lists about 20 threads, so all of them are read. A thread already stored from its
# board's feed is not stored twice.
SOURCES.append({
    'name': 'JamiiForums: Kero', 'kind': 'rss', 'is_forum': True, 'enabled': True,
    'language': 'sw', 'tanzania_focused': True,
    'url': 'https://www.jamiiforums.com/prefixess/kero.39/index.rss',
    'body_xpath': JAMIIFORUMS_FIRST_POST_XPATH, 'max_articles': 20, 'min_delay_seconds': 5.0,
})

SECTORS = {
    'education': ('Education', 'Elimu', {
        3: 'school schools teacher* pupil* students university universities tuition curriculum shule walimu mwalimu '
           'wanafunzi mwanafunzi elimu madarasa darasa vyuo chuo mitihani heslb',
        1: 'exam* classroom* college*',
    }),
    'infrastructure': ('Roads and transport', 'Barabara na usafiri', {
        3: 'road roads pothole* bridge bridges railway sgr bus buses daladala dart mwendokasi barabara barabarani '
           'madaraja reli usafiri tanroads tarura ferry kivuko',
        1: 'transport* airport port bandari daraja infrastructure miundombinu',
    }),
    'economy': ('Economy and trade', 'Uchumi na biashara', {
        3: 'inflation "cost of living" traders vendors tax taxes levy uchumi economy wafanyabiashara machinga '
           'wamachinga mikopo tozo "gharama za maisha" "mfumuko wa bei" sme smes shilling',
        1: 'market markets soko masoko loan* business* biashara prices bei kodi tra',
    }),
    'energy': ('Energy', 'Nishati', {
        3: 'electricity tanesco blackout* outage* "power cut*" "power supply" umeme ewura petrol diesel fuel nishati '
           'hydropower "mgawo wa umeme" lpg',
        1: 'energy power gesi gas mafuta',
    }),
    'agriculture': ('Agriculture, livestock and fisheries', 'Kilimo, mifugo na uvuvi', {
        3: 'farmer* crop crops fertiliser* fertilizer* harvest* livestock pastoralist* herder* fishermen fisheries '
           'irrigation wakulima mkulima kilimo mazao mbolea mavuno pembejeo mifugo wafugaji wavuvi uvuvi umwagiliaji '
           'cashew* korosho tumbaku tobacco pamba',
        1: 'agricultur* coffee kahawa maize mahindi',
    }),
    'health': ('Health', 'Afya', {
        3: 'hospital hospitals dispensar* doctors nurses patients maternal cholera malaria hospitali zahanati madaktari '
           'daktari wauguzi wagonjwa wajawazito kipindupindu "bima ya afya" nhif afya',
        1: 'health medicine* medical dawa clinic*',
    }),
    'water': ('Water and sanitation', 'Maji na usafi', {
        3: 'dawasa ruwasa "water supply" "clean water" "water shortage" "water project" sanitation borehole* sewage '
           '"majisafi" "maji safi" "uhaba wa maji" visima kisima',
        1: 'water maji',
    }),
    'land': ('Land and housing', 'Ardhi na makazi', {
        3: '"land dispute*" "land conflict*" "title deed*" eviction* evict* housing ardhi "mgogoro wa ardhi" '
           '"migogoro ya ardhi" "hati miliki" makazi',
        1: 'land demolition* fidia compensation viwanja nyumba',
    }),
    'governance': ('Governance and justice', 'Utawala na haki', {
        3: 'corruption rushwa ufisadi embezzle* "human rights" "haki za binadamu" abduct* utekaji "local government" '
           'councillor* madiwani diwani cag accountability mahakama court',
        1: 'election* uchaguzi transparency justice haki',
    }),
    'security': ('Public safety', 'Usalama', {
        3: 'crime robbery robberies killing* murder* uhalifu ujambazi mauaji polisi police gender-based "ukatili wa kijinsia"',
        1: 'security usalama',
    }),
    'environment': ('Environment and climate', 'Mazingira na tabianchi', {
        3: 'flood floods flooding drought deforest* pollution "climate change" mafuriko ukame "mabadiliko ya tabianchi" '
           'uchafuzi "human-wildlife" tembo',
        1: 'environment* mazingira wildlife wanyamapori',
    }),
    'employment': ('Jobs and labour', 'Ajira na kazi', {
        3: 'unemployment unemployed salary salaries wage wages pension* strike mgomo ajira mishahara mshahara '
           'pensheni nssf psssf "trade union*"',
        1: 'jobs workers wafanyakazi',
    }),
}

CONCERN_TERMS = {
    'complaint': {
        4: 'complain* lament* *lalamik* malalamiko "cry foul" "fed up"',
        3: 'kero dissatisf* frustrat* wamechoshwa tumechoka tunateseka inasikitisha hatujapata hatujalipwa '
           'wahusika hamuoni "mamlaka husika" "iingilie kati" "*michango mingi"',
    },
    'grievance': {
        4: 'grievance* petition* protest* demonstrat* maandamano injustice dhuluma unpaid "hawajalipwa" "hawajalipa"',
        3: 'migogoro mgogoro evict* wanadai "wanadai" demand* "haki zao" unfair "madai" "fidia"',
    },
    'challenge': {
        4: 'shortage* outage* blackout* "power cut*" uhaba ukosefu upungufu impassable overcrowd* "mgawo wa umeme" dropout*',
        3: '"lack of" "lacks" "poor roads" "poor state" ubovu crisis deteriorat* "high cost" "gharama kubwa" drought '
           'ukame flood* mafuriko "kukatika" kukatika collapse* "changamoto ya" unsafe homeless displaced *athirika "*kosa makazi" kikwazo vikwazo mzigo vilio '
           '"prices rise" "price hike*" "rising prices" "pressure on" "kupanda kwa bei" elusive',
        2: 'challenge challenges changamoto struggl* hardship* burden*',
    },
    'concern': {
        3: '"raise* concern*" "raising concern*" "concerned" "worried" wasiwasi hofu "fear*" alarm* '
           'waomba wanaomba "waiomba serikali" "wameiomba serikali"',
        2: 'concern concerns worry',
    },
}

VOICE_TERMS = (
    'residents villagers citizens locals parents farmers traders vendors passengers commuters patients pastoralists '
    'fishermen teachers students community communities "the public" taxpayers consumers '
    'wananchi wakazi wanakijiji wazazi wakulima wafanyabiashara abiria wagonjwa wafugaji wavuvi walimu wanafunzi '
    'watumiaji jamii watanzania wafanyakazi workers'
)


def parse_terms(text):
    """Split a term string, keeping "double quoted" phrases together."""
    return shlex.split(text)
