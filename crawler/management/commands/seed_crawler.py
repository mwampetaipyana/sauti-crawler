from django.core.management.base import BaseCommand
from django.db import transaction

from crawler.models import Keyword, Sector, Source
from crawler.seed_data import CONCERN_TERMS, EXCLUDES_NEWS, SECTORS, SOURCES, VOICE_TERMS, parse_terms


class Command(BaseCommand):
    help = 'Load starting sources, sectors and keywords. Existing rows and your edits are kept.'

    @transaction.atomic
    def handle(self, *args, **options):
        new_sources = 0
        for spec in SOURCES:
            spec = dict(spec)
            name = spec.pop('name')
            spec.setdefault('exclude_patterns', EXCLUDES_NEWS)
            new_sources += Source.objects.get_or_create(name=name, defaults=spec)[1]

        new_keywords = 0
        for slug, (name, name_sw, groups) in SECTORS.items():
            sector, _ = Sector.objects.get_or_create(slug=slug, defaults={'name': name, 'name_sw': name_sw})
            for weight, terms in groups.items():
                for term in parse_terms(terms):
                    new_keywords += Keyword.objects.get_or_create(
                        term=term, kind=Keyword.Kind.SECTOR, sector=sector, concern_type='',
                        defaults={'weight': weight},
                    )[1]

        for concern_type, groups in CONCERN_TERMS.items():
            for weight, terms in groups.items():
                for term in parse_terms(terms):
                    new_keywords += Keyword.objects.get_or_create(
                        term=term, kind=Keyword.Kind.CONCERN, sector=None, concern_type=concern_type,
                        defaults={'weight': weight},
                    )[1]

        for term in parse_terms(VOICE_TERMS):
            new_keywords += Keyword.objects.get_or_create(
                term=term, kind=Keyword.Kind.VOICE, sector=None, concern_type='', defaults={'weight': 1},
            )[1]

        self.stdout.write(self.style.SUCCESS(f'Added {new_sources} sources and {new_keywords} keywords.'))
