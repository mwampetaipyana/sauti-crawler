"""Rule-based classifier. Terms live in the database so analysts can tune them from the admin."""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from crawler.models import Keyword

TITLE_MULTIPLIER = 3
MAX_BODY_HITS_PER_TERM = 3
SECTOR_MIN_SCORE = 4
MAX_SECTORS = 3
CONCERN_MIN_SCORE = 6
# Weaker terms add to the score but cannot flag a story alone: "challenges" is often a verb.
STRONG_TERM_WEIGHT = 3
MIN_LINKED_SENTENCES = 2
MAX_STORED_TERMS = 12
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+|\n+')

SWAHILI_WORDS = frozenset(
    'na ya wa za kwa katika ni kuwa hiyo huo hii hilo cha vya la kama lakini pia sana wakati baada kutoka '
    'ambao ambayo amesema alisema wamesema serikali wananchi mkoa wilaya'.split()
)
ENGLISH_WORDS = frozenset(
    'the of and to in is that for with on was are as by from at this have has said were which their'.split()
)

# Region names, including places that map to them. Names that are also everyday Swahili
# words ("Mara" means "times", "Pwani" means "coast") count only with a phrase that marks a place.
REGION_PATTERNS = {
    'Arusha': r'Arusha', 'Dar es Salaam': r'Dar es Salaam|Dar-es-Salaam|Kinondoni|Temeke|Ilala|Ubungo|Kigamboni',
    'Dodoma': r'Dodoma', 'Geita': r'Geita', 'Iringa': r'Iringa', 'Kagera': r'Kagera|Bukoba',
    'Katavi': r'Katavi|Mpanda', 'Kigoma': r'Kigoma', 'Kilimanjaro': r'Kilimanjaro|Moshi', 'Lindi': r'Lindi',
    'Manyara': r'Manyara|Babati', 'Mara': r'(?:Mara Region|mkoa(?:ni)? wa Mara|mkoani Mara|Musoma|Tarime)',
    'Mbeya': r'Mbeya', 'Morogoro': r'Morogoro', 'Mtwara': r'Mtwara', 'Mwanza': r'Mwanza',
    'Njombe': r'Njombe', 'Pwani': r'(?:Coast Region|Pwani Region|mkoa(?:ni)? wa Pwani|mkoani Pwani|Kibaha|Bagamoyo)',
    'Rukwa': r'Rukwa|Sumbawanga', 'Ruvuma': r'Ruvuma|Songea', 'Shinyanga': r'Shinyanga|Kahama',
    'Simiyu': r'Simiyu|Bariadi', 'Singida': r'Singida', 'Songwe': r'Songwe|Tunduma', 'Tabora': r'Tabora',
    'Tanga': r'Tanga\b|Korogwe', 'Zanzibar': r'Zanzibar|Unguja|Pemba',
}
_REGIONS = {name: re.compile(rf'\b(?:{pattern})\b') for name, pattern in REGION_PATTERNS.items()}
_TANZANIA = re.compile(r'\bTanzania\w*|\bTZ\b|\bDodoma\b|\bDar es Salaam\b|\bZanzibar\w*|\bwatanzania\b', re.IGNORECASE)


@dataclass
class Classification:
    is_concern: bool = False
    concern_score: int = 0
    concern_type: str = ''
    region: str = ''
    language: str = ''
    sector_scores: dict = field(default_factory=dict)
    matched_terms: dict = field(default_factory=dict)


def compile_term(term):
    """`*` matches letters only, and a multi-word term tolerates any whitespace between words."""
    pattern = r'\s+'.join(re.escape(word).replace(r'\*', r'\w*') for word in term.split())
    return re.compile(rf'(?<!\w){pattern}(?!\w)', re.IGNORECASE)


class Lexicon:
    def __init__(self, keywords):
        self.entries = [(keyword, compile_term(keyword.term)) for keyword in keywords]

    @classmethod
    def load(cls):
        return cls(Keyword.objects.filter(active=True).select_related('sector'))


def _score_term(pattern, weight, title, body):
    """Return the term's score and the first word form it matched."""
    title_hits = pattern.findall(title)
    body_matches = pattern.findall(body)
    if not title_hits and not body_matches:
        return 0, ''
    score = weight * (len(title_hits) * TITLE_MULTIPLIER + min(len(body_matches), MAX_BODY_HITS_PER_TERM))
    return score, (title_hits or body_matches)[0].lower()


def _linked_sentences(body, concern_patterns, voice_patterns):
    """Count sentences that mention both a problem and the people affected by it."""
    if not concern_patterns or not voice_patterns:
        return 0
    return sum(
        any(p.search(sentence) for p in concern_patterns) and any(p.search(sentence) for p in voice_patterns)
        for sentence in _SENTENCE_BREAK.split(body)
    )


def classify(title, body, lexicon):
    concern_scores = Counter()
    title_has_concern = False
    sector_scores = Counter()
    concern_terms, voice_terms = Counter(), Counter()
    sector_terms = defaultdict(Counter)

    concern_patterns, voice_patterns = [], []

    for keyword, pattern in lexicon.entries:
        score, word = _score_term(pattern, keyword.weight, title, body)
        if not score:
            continue
        if keyword.kind == Keyword.Kind.CONCERN:
            concern_scores[keyword.concern_type] += score
            concern_terms[word] += score
            if keyword.weight >= STRONG_TERM_WEIGHT:
                concern_patterns.append(pattern)
                title_has_concern = title_has_concern or bool(pattern.search(title))
        elif keyword.kind == Keyword.Kind.VOICE:
            voice_patterns.append(pattern)
            voice_terms[word] += score
        else:
            sector_scores[keyword.sector.slug] += score
            sector_terms[keyword.sector.slug][word] += score

    total = sum(concern_scores.values())
    top_sectors = {
        slug: score for slug, score in sector_scores.most_common(MAX_SECTORS) if score >= SECTOR_MIN_SCORE
    }
    # Hardship vocabulary alone also fits officials' speeches. Require that the headline is about
    # the problem, or that several sentences tie the problem to the people it affects.
    is_concern = total >= CONCERN_MIN_SCORE and (
        title_has_concern
        or _linked_sentences(body, concern_patterns, voice_patterns) >= MIN_LINKED_SENTENCES
    )
    return Classification(
        is_concern=is_concern,
        concern_score=total,
        concern_type=concern_scores.most_common(1)[0][0] if is_concern else '',
        region=detect_region(f'{title}\n{body}'),
        language=detect_language(f'{title} {body}'),
        sector_scores=top_sectors,
        matched_terms={
            'concern': [word for word, _ in concern_terms.most_common(MAX_STORED_TERMS)],
            'voice': [word for word, _ in voice_terms.most_common(MAX_STORED_TERMS)],
            'sectors': {
                slug: [word for word, _ in sector_terms[slug].most_common(MAX_STORED_TERMS)]
                for slug in top_sectors
            },
        },
    )


def detect_language(text):
    words = re.findall(r"[^\W\d_]+", text.lower())[:800]
    swahili = sum(word in SWAHILI_WORDS for word in words)
    english = sum(word in ENGLISH_WORDS for word in words)
    if not swahili and not english:
        return ''
    return 'sw' if swahili > english else 'en'


def detect_region(text):
    counts = Counter({name: len(pattern.findall(text)) for name, pattern in _REGIONS.items()})
    name, count = counts.most_common(1)[0]
    return name if count else ''


def mentions_tanzania(title, body):
    return bool(_TANZANIA.search(title)) or len(_TANZANIA.findall(body)) >= 2
