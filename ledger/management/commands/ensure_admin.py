from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Създава администратор интерактивно само ако няма активен такъв.'

    def handle(self, *args, **options):
        if get_user_model().objects.filter(is_superuser=True, is_active=True).exists():
            self.stdout.write('Използвайте съществуващия администраторски профил.')
            return
        call_command('createsuperuser', interactive=True)
