import json
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import connection, transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.utils import timezone
from .models import (Audit, Course, Disbursement, Expense, FinancialRule, HistoricalValue,
                     Participant, Payment, Period, Transfer)
from .money import ZERO, allocate, money

def serial(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))

def audit(user, action, obj, before=None, after=None, reason=''):
    return Audit.objects.create(actor=user, action=action, target=f'{obj._meta.label}:{obj.pk}',
                                before=serial(before or {}), after=serial(after or {}), reason=reason)

def writer(user):
    if not user.is_authenticated or not (user.is_superuser or user.groups.filter(name='Счетоводител').exists()):
        raise PermissionDenied('Необходими са права за финансови промени.')

def admin(user):
    if not user.is_superuser: raise PermissionDenied('Необходими са администраторски права.')

def financial_lock():
    # All financial mutations and closures serialize across workers on PostgreSQL.
    if connection.vendor == 'postgresql':
        with connection.cursor() as c: c.execute('SELECT pg_advisory_xact_lock(79483201)')

def open_date(value):
    if Period.objects.filter(historical=False, state__in=['approved','locked'], start__lte=value, end__gte=value).exists():
        raise ValidationError('Периодът е утвърден или заключен. Необходима е обоснована корекция.')

def rule_for(course, day):
    qs = FinancialRule.objects.filter(confirmed=True,valid_from__lte=day).filter(Q(valid_until__isnull=True)|Q(valid_until__gte=day))
    specific = list(qs.filter(course=course))
    rules = specific or list(qs.filter(course__isnull=True))
    if len(rules)!=1: raise ValidationError('Необходимо е точно едно приложимо потвърдено финансово правило.')
    return rules[0]

@transaction.atomic
def confirm_payment(pk, user):
    writer(user); financial_lock()
    p=Payment.objects.select_for_update().get(pk=pk)
    if p.confirmed: raise ValidationError('Плащането вече е потвърдено.')
    open_date(p.date); p.full_clean()
    if p.kind=='refund':
        original=Payment.objects.select_for_update().get(pk=p.refund_of_id)
        refunded=sum((x.amount_eur for x in Payment.objects.filter(refund_of=original,confirmed=True)),ZERO)
        if refunded+p.amount_eur>original.amount_eur: raise ValidationError('Възстановяването надвишава платеното.')
        if p.participant_id!=original.participant_id: raise ValidationError('Възстановяването трябва да е за същия участник.')
        p.rule=original.rule
        # Allocate cumulatively so the final refund reverses every original cent.
        alloc={}
        for key in ['university','faculty','center']:
            already=sum((Decimal(x.allocation[key]) for x in Payment.objects.filter(refund_of=original,confirmed=True)),ZERO)
            cumulative=money(Decimal(original.allocation[key])*(refunded+p.amount_eur)/original.amount_eur)
            alloc[key]=str(cumulative-already)
        alloc['remaining']=str(p.amount_eur-sum((Decimal(v) for v in alloc.values()),ZERO))
        alloc['rounding']='0.00'
    else:
        p.rule=rule_for(p.course,p.date)
        shares,remain,delta=allocate(p.amount_eur,[p.rule.university,p.rule.faculty,p.rule.center])
        alloc=dict(zip(['university','faculty','center'],map(str,shares)))
        alloc.update(remaining=str(remain),rounding=str(delta))
    Payment.objects.filter(pk=p.pk).update(confirmed=True,rule=p.rule,allocation=alloc)
    audit(user,'Потвърдено плащане',p,after={'amount_eur':p.amount_eur,'allocation':alloc,'reference':p.reference})

@transaction.atomic
def pay_expense(disbursement,user):
    writer(user); financial_lock(); open_date(disbursement.date)
    exp=Expense.objects.select_for_update().get(pk=disbursement.expense_id)
    if exp.state not in ('approved','due'): raise ValidationError('Разходът не е одобрен.')
    if exp.paid+disbursement.amount>exp.total: raise ValidationError('Плащането надвишава задължението.')
    disbursement.created_by=user; disbursement.save()
    audit(user,'Изплатен разход',disbursement,after=model_to_dict(disbursement))

def sum_rows(rows, key):
    return sum((getattr(x,key) for x in rows),ZERO)

def selected_payments(filters):
    qs=Payment.objects.filter(confirmed=True).select_related('course','rule','participant')
    if filters.get('year'): qs=qs.filter(date__year=filters['year'])
    if filters.get('month'): qs=qs.filter(date__month=filters['month'])
    if filters.get('course'): qs=qs.filter(course_id=filters['course'])
    if filters.get('lecturer'): qs=qs.filter(course__lecturers__pk=filters['lecturer']).distinct()
    if filters.get('kind'): qs=qs.filter(kind=filters['kind'])
    return list(qs)

def historical_revenues(filters):
    qs=HistoricalValue.objects.filter(kind='revenue').select_related('period','course')
    if filters.get('year'): qs=qs.filter(period__end__year=filters['year'])
    if filters.get('month'): qs=qs.filter(period__end__month=filters['month'])
    if filters.get('course'): qs=qs.filter(course_id=filters['course'])
    if filters.get('lecturer'): qs=qs.filter(course__lecturers__pk=filters['lecturer']).distinct()
    if filters.get('kind')=='recording': qs=qs.filter(metadata__income_kind='recording')
    if filters.get('kind')=='tuition': qs=qs.exclude(metadata__income_kind='recording')
    if filters.get('kind')=='refund': return []
    return list(qs)

def dashboard_data(filters):
    payments=selected_payments(filters); historic=historical_revenues(filters)
    historical=sum((v.converted_eur for v in historic),ZERO)
    actual=sum((p.signed_eur for p in payments),ZERO)
    # Totals from source allocation lines only for complete periods; do not fabricate course allocations.
    period_ids={v.period_id for v in historic}
    distribution={k:ZERO for k in ['university','faculty','center']}
    hist_distribution={k:ZERO for k in distribution}
    if not any(filters.get(k) for k in ['course','lecturer','kind']):
        for v in HistoricalValue.objects.filter(period_id__in=period_ids,kind__in=distribution):
            hist_distribution[v.kind]+=v.converted_eur
    for p in payments:
        for k in distribution: distribution[k]+=Decimal(p.allocation[k])*(-1 if p.kind=='refund' else 1)
    expenses=Expense.objects.all().select_related('course').prefetch_related('disbursements')
    transfers=Transfer.objects.all(); disbursements=Disbursement.objects.all()
    participants=Participant.objects.exclude(state='cancelled')
    for field in ['year','month']:
        if filters.get(field):
            participants=participants.filter(Q(**{f'due_date__{field}':filters[field]})|Q(due_date__isnull=True,**{f'created_at__{field}':filters[field]}))
            expenses=expenses.filter(**{f'date__{field}':filters[field]})
            transfers=transfers.filter(**{f'date__{field}':filters[field]})
            disbursements=disbursements.filter(**{f'date__{field}':filters[field]})
    if filters.get('course'):
        expenses=expenses.filter(course_id=filters['course']); disbursements=disbursements.filter(expense__course_id=filters['course'])
        participants=participants.filter(course_id=filters['course'])
    if filters.get('lecturer'):
        expenses=expenses.filter(course__lecturers__pk=filters['lecturer']).distinct()
        disbursements=disbursements.filter(expense__course__lecturers__pk=filters['lecturer']).distinct()
        participants=participants.filter(course__lecturers__pk=filters['lecturer']).distinct()
    expenses=list(expenses)
    paid=sum_rows(disbursements,'amount'); transferred=sum_rows(transfers,'amount')
    destinations=[]
    for key,label in [('university','Университет'),('faculty','Педагогически факултет'),('center','Пространството ПФ+')]:
        sent=sum((t.amount for t in transfers if t.destination==key),ZERO)
        destinations.append({'name':label,'historic':hist_distribution[key],'accrued':distribution[key],
                             'transferred':sent,'untransferred':distribution[key]-sent})
    months={}
    for v in historic:
        key=v.period.end.strftime('%Y-%m'); months[key]=months.get(key,ZERO)+v.converted_eur
    for p in payments:
        key=p.date.strftime('%Y-%m'); months[key]=months.get(key,ZERO)+p.signed_eur
    maximum=max([abs(v) for v in months.values()]+[Decimal(1)])
    chart=[{'month':k,'value':v,'width':int(abs(v)*100/maximum)} for k,v in sorted(months.items())]
    costs={key:sum((x.amount for x in expenses if x.category==key),ZERO) for key,_ in Expense._meta.get_field('category').choices}
    costs['insurance']+=sum((x.insurance for x in expenses),ZERO)
    historic_costs={}
    if not any(filters.get(k) for k in ['course','lecturer','kind']):
        for v in HistoricalValue.objects.filter(period_id__in=period_ids,kind__in=['staff','external','pensioner','insurance_staff','insurance_external','insurance_pensioner','invoice']):
            historic_costs[v.kind]=historic_costs.get(v.kind,ZERO)+v.converted_eur
    course_stats={}
    for v in historic:
        key=v.course_id
        course_stats.setdefault(key,{'course':v.course,'historical':ZERO,'actual':ZERO})['historical']+=v.converted_eur
    for p in payments:
        course_stats.setdefault(p.course_id,{'course':p.course,'historical':ZERO,'actual':ZERO})['actual']+=p.signed_eur
    for course_id,stat in course_stats.items():
        stat['allocated']=sum((Decimal(p.allocation['remaining'])*(-1 if p.kind=='refund' else 1) for p in payments if p.course_id==course_id),ZERO)
        stat['cost']=sum((e.total for e in expenses if e.course_id==course_id and e.state in ('approved','due')),ZERO)
        stat['result']=stat['allocated']-stat['cost']
        stat['planned_revenue']=stat['course'].expected_count*stat['course'].expected_price
        stat['planned_result']=budget(stat['course']).get('result')
        stat['reported_fees']=sum((sum(f['count'] for f in v.metadata.get('fees',[])) for v in historic if v.course_id==course_id),0)
    alltime=sum((v.converted_eur for v in HistoricalValue.objects.filter(kind='revenue')),ZERO)+sum((p.signed_eur for p in Payment.objects.filter(confirmed=True)),ZERO)
    return dict(total=historical+actual,alltime=alltime,historical=historical,actual=actual,payments=payments,
                historical_rows=historic,distribution=destinations,expenses=expenses,paid=paid,transferred=transferred,
                cash_movement=actual-paid-transferred,expected=sum((max(p.due-p.paid,ZERO) for p in participants),ZERO),
                planned=sum((x.total for x in expenses if x.state=='planned'),ZERO),
                approved=sum((x.total for x in expenses if x.state in ['approved','due']),ZERO),
                chart=chart,costs=costs,historic_costs=historic_costs,course_stats=list(course_stats.values()),
                recording=sum((v.converted_eur for v in historic if v.metadata.get('income_kind')=='recording'),ZERO)+sum((p.signed_eur for p in payments if p.kind=='recording'),ZERO))

def period_totals(period):
    if period.historical:
        result={v.kind:str(v.effective_eur)
                for v in period.values.exclude(kind='revenue')}
        result['revenues']=[{'label':v.label,'amount':str(v.effective_eur),'row_id':v.pk} for v in period.values.filter(kind='revenue')]
        result['source_id']=period.source_id
        result['expense_people']=period.snapshot.get('expense_people',[])
        result['officials']=period.snapshot.get('officials',{})
        result['provenance']={v.kind:f'HistoricalValue:{v.pk}' for v in period.values.exclude(kind='revenue')}
        result['provenance']['adjustment_ids']=list(period.values.filter(adjustments__isnull=False).values_list('adjustments__id',flat=True))
        result['provenance']['source_sha256']=period.source.sha256
        return result
    previous=Period.objects.filter(end__lt=period.start,state__in=['approved','locked']).order_by('-end','-pk').first()
    # Historical course reports may overlap; only the center's latest approved aggregate carries forward.
    opening=Decimal(previous.snapshot.get('closing','0')) if previous else ZERO
    payments=list(Payment.objects.filter(confirmed=True,date__range=[period.start,period.end]).select_related('course'))
    expenses=list(Expense.objects.filter(date__range=[period.start,period.end],state__in=['approved','due']))
    total=sum((p.signed_eur for p in payments),ZERO)
    result={'total':str(total),'opening':str(opening)}
    for k in ['university','faculty','center','remaining','rounding']:
        result[k]=str(sum((Decimal(p.allocation.get(k,'0'))*(-1 if p.kind=='refund' else 1) for p in payments),ZERO))
    result['available']=str(Decimal(result['remaining'])+opening)
    for key in ['staff','external','pensioner','invoice','other','insurance']:
        result[key]=str(sum((e.amount for e in expenses if e.category==key),ZERO))
    for key in ['staff','external','pensioner']:
        result['insurance_'+key]=str(sum((e.insurance for e in expenses if e.category==key),ZERO))
    result['costs']=str(sum((e.total for e in expenses),ZERO))
    result['closing']=str(Decimal(result['available'])-Decimal(result['costs']))
    result['revenues']=[{'label':str(p.course),'amount':str(p.signed_eur),'payment_id':p.pk} for p in payments]
    result['expense_people']=[{'name':e.payee,'amount':str(e.amount),'category':e.category,'expense_id':e.pk} for e in expenses]
    result['rule_rates']=[{'id':r.pk,'name':r.name,'university':str(r.university),'faculty':str(r.faculty),'center':str(r.center),
                           'gross':str(sum((p.signed_eur for p in payments if p.rule_id==r.pk),ZERO))}
                          for r in FinancialRule.objects.filter(pk__in={p.rule_id for p in payments})]
    result['compensation_rates']=[{'id':e.rate_id,'category':e.category,'percent':str(e.rate.percent)} for e in expenses if e.rate_id]
    result['provenance']={'payment_ids':[p.pk for p in payments],'expense_ids':[e.pk for e in expenses],
                          'previous_period_id':previous.pk if previous else None,
                          'rule_ids':sorted({p.rule_id for p in payments})}
    return result

@transaction.atomic
def close_period(pk,user,action,reason=''):
    admin(user); financial_lock(); p=Period.objects.select_for_update().get(pk=pk)
    before={'state':p.state,'revision':p.revision,'snapshot':p.snapshot}
    if action=='reopen':
        if p.state=='draft' or not reason.strip(): raise ValidationError('Необходимо е основание за корекция на утвърден отчет.')
        if Period.objects.filter(start__gt=p.end,state__in=['approved','locked']).exists(): raise ValidationError('Първо отворете по-късните утвърдени отчети, за да се запази веригата на остатъците.')
        p.state='draft'; p.revision+=1; p.approved_by=None; p.approved_at=None
    elif action=='approve':
        if p.state!='draft': raise ValidationError('Само проект може да бъде утвърден.')
        if p.discrepancies.filter(state='open').exists(): raise ValidationError('Първо разгледайте откритите несъответствия.')
        if not reason.strip(): raise ValidationError('Посочете номер и основание за действителното утвърждаване.')
        if not p.historical:
            previous=Period.objects.filter(end__lt=p.start).order_by('-end','-pk').first()
            if previous and previous.state=='draft': raise ValidationError('Предходният наличен отчет още не е утвърден; началният остатък не е потвърден.')
        p.snapshot=period_totals(p); p.state='approved'; p.approved_by=user; p.approved_at=timezone.now()
    elif action=='lock':
        if p.state!='approved': raise ValidationError('Необходимо е действително утвърждаване преди заключване.')
        p.state='locked'
    else: raise ValidationError('Невалидно действие.')
    p.save(); audit(user,'Отчет: '+action,p,before,{'state':p.state,'snapshot':p.snapshot,'revision':p.revision},reason)
    return p

def budget(course):
    from math import ceil
    if not course.start: return {'error':'Задайте дата за избор на приложимо финансово правило.'}
    try: rule=rule_for(course,timezone.localtime(course.start).date())
    except ValidationError as e: return {'error':'; '.join(e.messages)}
    unit_shares,unit_remaining,delta=allocate(course.expected_price,[rule.university,rule.faculty,rule.center])
    shares=[s*course.expected_count for s in unit_shares]; remaining=unit_remaining*course.expected_count
    margin=unit_remaining
    planned=list(Expense.objects.filter(course=course,planned_total__isnull=False))
    cost=sum((e.planned_total for e in planned),ZERO) if planned else course.expected_cost
    return {'revenue':course.expected_price*course.expected_count,'deductions':sum(shares),
            'remaining':remaining,'cost':cost,'result':remaining-cost,
            'cost_basis':'По планови разходни записи' if planned else 'По обобщената планова оценка',
            'break_even':ceil(cost/margin) if margin>0 else None}
