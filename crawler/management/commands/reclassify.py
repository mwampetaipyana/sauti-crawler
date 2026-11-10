from django.core.management.base import BaseCommand

from crawler.models import Article, Sector
from crawler.services.classifier import Lexicon, classify
from crawler.services.pipeline import apply_classification


class Command(BaseCommand):
    help = 'Re-run the classifier on stored articles, without fetching. Use after editing keywords.'

    def handle(self, *args, **options):
        lexicon = Lexicon.load()
        sector_ids = dict(Sector.objects.values_list('slug', 'id'))
        total = flagged = 0
        for article in Article.objects.select_related('source').iterator():
            result = classify(article.title, article.body, lexicon)
            apply_classification(article, result, sector_ids)
            total += 1
            flagged += int(result.is_concern)
        self.stdout.write(self.style.SUCCESS(f'Reclassified {total} articles; {flagged} flagged as concerns.'))
