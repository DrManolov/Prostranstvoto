from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError

CENT = Decimal('0.01')
FX = Decimal('1.95583')
ZERO = Decimal('0.00')

def decimal(value):
    if isinstance(value, float):
        raise TypeError('Финансови стойности не могат да бъдат floating-point.')
    return Decimal(value)

def money(value):
    return decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)

def eur(value, currency):
    if currency not in ('BGN', 'EUR'):
        raise ValidationError('Поддържани валути: EUR и BGN.')
    return money(decimal(value) / FX if currency == 'BGN' else value)

def conversion_delta(value, currency):
    exact = decimal(value) / FX if currency == 'BGN' else decimal(value)
    return eur(value, currency) - exact

def allocate(amount, rates):
    amount = money(amount)
    rates = [decimal(x) for x in rates]
    if any(x < 0 for x in rates) or sum(rates) > 100:
        raise ValidationError('Невалидно разпределение на приходите.')
    shares = [money(amount * x / 100) for x in rates]
    remaining = money(amount - sum(shares))
    delta = remaining - money(amount * (100-sum(rates)) / 100)
    return shares, remaining, delta
