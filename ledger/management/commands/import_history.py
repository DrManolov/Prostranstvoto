from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from ledger.importer import import_document, reconcile_chain, analysis_markdown

class Command(BaseCommand):
    help='Идемпотентен импорт на 14-те оригинални DOCX източника.'
    def add_arguments(self,parser):
        parser.add_argument('--analysis-output',type=Path,default=None,help='Изричен път за отчет; по подразбиране е в игнорираната runtime директория.')
    def handle(self,*args,**options):
        count=0
        for path in sorted((settings.BASE_DIR/'data'/'originals').glob('*.docx')):
            if import_document(path): count+=1
        reconcile_chain()
        target=options.get('analysis_output') or settings.RUNTIME/'historical-analysis.md'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(analysis_markdown())
        self.stdout.write(f'Импортирани нови документи: {count}')
