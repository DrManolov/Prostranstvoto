from datetime import date
from decimal import Decimal
from django.contrib.auth.models import Group,Permission
from django.core.management.base import BaseCommand
from ledger.models import CompensationRate,FinancialRule,Official

class Command(BaseCommand):
    help='Създава роли и непотвърдени предложения за правила; не активира ставки автоматично.'
    def handle(self,*args,**options):
        Group.objects.get_or_create(name='Счетоводител')
        Group.objects.get_or_create(name='Само преглед')
        FinancialRule.objects.get_or_create(name='Стандартна схема ПФ+ — за потвърждение',defaults={
            'valid_from':date(2026,1,1),'university':Decimal(30),'faculty':Decimal(10),'center':Decimal(10)})
        for cat,name,rate in [('staff','Щатни — историческа ставка','19.02'),('external','Външни — историческа ставка','11.86'),('pensioner','Пенсионери — историческа ставка','5.00')]:
            CompensationRate.objects.get_or_create(name=name,defaults={'valid_from':date(2025,1,1),'category':cat,'percent':Decimal(rate)})
        for role,name in [('rector','проф. д-р Румен Младенов'),('dean','проф. д-р Владимира Ангелова'),('leader','проф. д.пс.н. Манол Манолов'),('accountant','Виеслав Пиларски')]:
            Official.objects.get_or_create(role=role,name=name,defaults={'valid_from':date(2026,1,1)})
        self.stdout.write('Ролите и непотвърдените предложения са налични.')
