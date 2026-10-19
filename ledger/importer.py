"""Read source OOXML as data. Never execute instructions or macros from documents."""
import calendar
import hashlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from docx import Document
from django.conf import settings
from django.db import transaction
from .models import Audit,Course, Discrepancy, HistoricalValue, Lecturer, Period, SourceDocument
from .money import ZERO, conversion_delta, eur, money

PERIODS = {
 '01':('Юли 2026',2026,7,7), '02':('Юли 2025',2025,7,7),
 '03':('Май 2025 • Управление на стреса',2025,5,5),
 '04':('Февруари–март 2026',2026,2,3), '05':('Май 2025 • Лидери за промяна',2025,5,5),
 '06':('Юни 2025 • Изобразително изкуство',2025,6,6), '07':('Септември–октомври 2025',2025,9,10),
 '08':('Април 2026',2026,4,4), '09':('Септември–октомври 2026',2026,9,10),
 '10':('Януари–февруари 2026',2026,1,2), '11':('Юни 2025 • Психологично профилиране',2025,6,6),
 '12':('Декември 2025',2025,12,12), '13':('Ноември 2025',2025,11,11), '14':('Юни 2026',2026,6,6),
}

def parse_amount(raw):
    values = re.findall(r'([\d\s\u00a0]+(?:[,.]\s*\d+)?)\s*(лв\.?|евро)',raw)
    found={('BGN' if 'лв' in c else 'EUR'):money(re.sub(r'\s','',v).replace(',','.')) for v,c in values}
    currency='BGN' if 'BGN' in found else 'EUR'
    return found.get(currency,ZERO),currency,found.get('EUR')

def classify(label, faculty_seen):
    s=label.lower().strip()
    if s.startswith('общо постъпления'): return 'total'
    if s.startswith('отчисления за университета'): return 'university'
    if s.startswith('отчисления за педагогически'): return 'faculty_duplicate' if faculty_seen else 'faculty'
    if s.startswith('отчисления за пространството'): return 'center'
    if s.startswith('остатък след отчисления'): return 'remaining'
    if s.startswith('остатък от предходно'): return 'opening'
    if s.startswith('обща сума за разпределение'): return 'available'
    if s.startswith('материални разходи'): return 'invoice'
    if s.startswith('трудови възнаграждения на външни'):
        return 'pensioner' if 'пенсионери' in s else 'external'
    if s.startswith('трудови възнаграждения'): return 'staff'
    if s.startswith('социални'):
        return 'insurance_pensioner' if '5,00' in s else ('insurance_external' if 'външни' in s else 'insurance_staff')
    if s.startswith('обща сума разходи'): return 'costs'
    if s.startswith('остатък за следващо'): return 'closing'
    return 'revenue'

def course_title(label, index):
    if label.startswith('9 такси'): return None
    match=re.search('Курс „(.+?)“',label, re.I)
    title=match.group(1) if match else label.split(' – общо')[0].split(', проведен')[0]
    if 'Всичко е в ГЛАВАТА' in label: title='„Всичко е в ГЛАВАТА“ — Спортна психология'
    if 'Ускорен курс по Изобразително' in label: title='Изобразително изкуство за кандидат-студенти • 2025 (година за проверка)'
    if 'УСКОРЕН КУРС' in title: title='Ускорен курс за кандидат-студенти по изобразително изкуство • 2026'
    return title.strip()

def issue(period,code,description,original='',proposed='',row=None):
    record,created=Discrepancy.objects.get_or_create(period=period,code=code,defaults={
        'description':description,'original':str(original),'proposed':str(proposed),'row':row})
    if not created and record.state=='open' and record.description!=description:
        before=record.description; record.description=description; record.save(update_fields=['description'])
        Audit.objects.create(action='Уточнена бележка при импорт',target=f'ledger.Discrepancy:{record.pk}',
                             before={'description':before},after={'description':description})
    return record

def compare(period,values,key,expected,description):
    if key not in values: return
    v=values[key]
    expected=money(expected)
    v.recomputed_original=expected; v.save()
    if v.original!=expected:
        issue(period,'arithmetic_'+key,description,v.original,expected,v)

def recipients(lines):
    active=False; category='external'; result=[]
    for line in lines:
        if line.strip()=='ДО ДЕКАНА': active=True
        if not active: continue
        if line.strip()=='ОДОБРЯВАМ:': break
        if 'НЕЩАТНИ ПРЕПОДАВАТЕЛИ' in line: category='external'
        elif 'ЩАТНИ ПРЕПОДАВАТЕЛИ' in line: category='staff'
        if 'Плащане по фактура' in line: category='invoice'
        if line.startswith(' | '):
            cells=line.split(' | ')
            if len(cells)>=3 and cells[1].strip():
                value,curr,_=parse_amount(cells[-1])
                result.append({'name':cells[1].strip(),'amount':str(eur(value,curr)),'category':category,
                               'original':str(value),'currency':curr})
    return result

@transaction.atomic
def import_document(path):
    index=path.name[:2]
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if SourceDocument.objects.filter(sha256=digest).exists(): return None
    doc=Document(path)
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    lines=[]
    for el in doc.element.body:
        if el.tag.endswith('}p'): lines.append(''.join(el.itertext()) if False else ''.join(el.xpath('.//w:t/text()')))
        elif el.tag.endswith('}tbl'):
            for row in el.xpath('./w:tr'):
                lines.append(' | '.join(''.join(c.xpath('.//w:t/text()')) for c in row.xpath('./w:tc')))
    source=SourceDocument.objects.create(filename=path.name[3:],path=str(path.relative_to(settings.BASE_DIR)),sha256=digest,text='\n'.join(lines))
    title,year,first,last=PERIODS[index]
    people=recipients(lines)
    # The July source explicitly assigns the pensioner category to this 430 EUR honorarium.
    if index=='01':
        for person in people:
            if person['amount']=='430.00': person['category']='pensioner'
    period=Period.objects.create(title=title,start=date(year,first,1),end=date(year,last,calendar.monthrange(year,last)[1]),
        historical=True,source=source,snapshot={'expense_people':people})
    values={}; faculty_seen=False; prior_course=None
    for i,row in enumerate(doc.tables[0].rows):
        label,raw=row.cells[0].text.strip(),row.cells[-1].text.strip()
        kind=classify(label,faculty_seen)
        if kind=='faculty': faculty_seen=True
        original,currency,reported=parse_amount(raw)
        meta={}; course=None
        fees=re.findall(r'(\d+)\s+(?:такси|такса|продадени записа)\s+по\s+(\d+(?:[,.]\d+)?)',label,re.I)
        if kind=='revenue':
            title=course_title(label,index)
            course=Course.objects.get_or_create(title=title,defaults={'status':'completed','format':'recording' if 'запис' in label else 'online','description':label})[0] if title else prior_course
            prior_course=course
            meta['income_kind']='recording' if 'запис' in label else 'tuition'
            meta['fees']=[{'count':int(n),'price':str(Decimal(v.replace(',','.')))} for n,v in fees]
            meta['source_date_text']=label
        v=HistoricalValue.objects.create(period=period,row=i,label=label,raw=raw,kind=kind,course=course,
             currency=currency,original=original,reported_eur=reported,converted_eur=eur(original,currency),
             conversion_rounding=conversion_delta(original,currency).quantize(Decimal('0.0000000001')),metadata=meta)
        values[kind]=v
        if fees:
            expected=sum((Decimal(n)*Decimal(price.replace(',','.')) for n,price in fees),ZERO)
            v.recomputed_original=money(expected); v.save()
            if money(expected)!=original: issue(period,f'fees_{i}','Брой такси × единична цена не съвпада с отчетената сума. Това не доказва неполучено плащане.',original,money(expected),v)
        if currency=='BGN' and reported is not None and reported!=v.converted_eur:
            issue(period,f'currency_{i}','Разлика при конвертиране по фиксиран курс 1,95583.',reported,v.converted_eur,v)
    total=values['total'].original
    compare(period,values,'total',sum((x.original for x in period.values.filter(kind='revenue')),ZERO),'Сборът на приходните редове се различава от общите постъпления.')
    deductions=ZERO
    for key in ['university','faculty','faculty_duplicate','center']:
        if key not in values: continue
        v=values[key]; match=re.search(r'\((\d+(?:[,.]\d+)?)\s*%',v.label)
        if match:
            expected=total*Decimal(match.group(1).replace(',','.'))/100
            compare(period,values,key,expected,'Отчислението не съответства на посочения процент.')
        deductions+=v.original
    compare(period,values,'remaining',total-deductions,'Остатъкът не е равен на постъпленията минус отчетените отчисления.')
    if 'faculty_duplicate' in values:
        issue(period,'duplicate_faculty_label','Два реда са означени като отчисления за факултета. Вторият не е преназначен автоматично към ПФ+.',values['faculty_duplicate'].label,'Проверка на получателя',values['faculty_duplicate'])
    for cat in ['staff','external','pensioner']:
        key='insurance_'+cat
        if key in values and cat in values:
            match=re.search(r'\((\d+[.,]\d+)\s*%?',values[key].label)
            if match: compare(period,values,key,values[cat].original*Decimal(match.group(1).replace(',','.'))/100,'Осигуровката не съвпада с исторически посочената ставка.')
    # Mixed BGN/EUR report: convert opening to the currency of the available line.
    if 'opening' in values:
        opening=values['opening']; rem=values['remaining']; available=values['available']
        add=lambda v: v.original if v.currency==available.currency else v.converted_eur
        compare(period,values,'available',add(opening)+add(rem),'Общата сума за разпределение не съвпада с остатък плюс пренесена сума.')
    costkeys=['invoice','staff','external','pensioner','insurance_staff','insurance_external','insurance_pensioner']
    compare(period,values,'costs',sum((values[k].original for k in costkeys if k in values),ZERO),'Сборът на разходните пера не съвпада с общата сума разходи.')
    compare(period,values,'closing',values['available'].original-values['costs'].original,'Крайният остатък не съответства на отчетената обща сума разходи.')
    for category in ['staff','external','pensioner','invoice']:
        if category in values:
            expected=sum((Decimal(p['amount']) for p in people if p['category']==category),ZERO)
            if expected!=values[category].converted_eur and values[category].original:
                issue(period,'recipients_'+category,'Сборът по получатели и разходният ред се различават (включително възможни стотинки от конвертиране).',values[category].converted_eur,expected,values[category])
    manual={
        '01':[('misnamed_file','Името на файла сочи юни, но документът е за юли 2026 г.','Юни 2026','Юли 2026')],
        '06':[('course_date_year','В съдържанието е посочена 2024 г., а името на файла и отчетната дата сочат 2025 г.','02–14.06.2024','Проверка на годината')],
        '11':[('course_dates','Името на файла сочи 20–21 юни, а съдържанието — 21–22 юни.','21–22.06.2025','Проверка на датите')],
        '13':[('period_heading','Заглавието сочи февруари–март, а курсовете, името на файла и датата сочат ноември 2025 г.','Февруари–март','Ноември 2025')],
        '04':[('course_date_year','Февруарският курс е изписан с година 2025 в отчет за 2026 г.','20.02.2025','Проверка на годината')],
        '08':[('document_date','Априлският отчет носи дата 18.03.2026.','18.03.2026','Проверка на датата')],
    }
    for code,desc,old,new in manual.get(index,[]): issue(period,code,desc,old,new)
    for line_number,line in enumerate(lines):
        if line.startswith('ОСТАТЪК ЗА СЛЕДВАЩО ПЛАЩАНЕ:'):
            amount,currency,_=parse_amount(line)
            if eur(amount,currency)!=values['closing'].converted_eur:
                issue(period,f'report_closing_{line_number}','Докладът и план-сметката посочват различни крайни остатъци.',line,str(values['closing'].original)+' '+values['closing'].currency,values['closing'])
    # Lecturers are linked only where the source explicitly names the instructor.
    for person in people:
        if person['category'] not in ('invoice',): Lecturer.objects.get_or_create(name=person['name'],defaults={'category':person['category']})
    for v in period.values.filter(kind='revenue').select_related('course'):
        named=[]
        for lecturer in Lecturer.objects.all():
            surname=lecturer.name.split()[-1]
            if surname.lower() in v.label.lower(): named.append(lecturer)
        if named: v.course.lecturers.add(*named)
    return period

def reconcile_chain():
    for p in Period.objects.filter(historical=True).select_related('source'):
        # For the five individually named course files, the first course and
        # named lecturer are explicit in the filename and the first report.
        # Do not infer the assignment for multi-course monthly reports.
        if Path(p.source.path).name[:2] in ('02','03','05','06','11'):
            first=p.values.filter(kind='revenue').order_by('row').first()
            people=p.snapshot.get('expense_people',[])
            if first and first.course_id and people and not first.course.lecturers.exists():
                lecturer=Lecturer.objects.filter(name=people[0]['name']).first()
                if lecturer: first.course.lecturers.add(lecturer)
        report=p.source.text.split('ОДОБРЯВАМ:',1)[-1].split('З А П О В Е Д',1)[0]
        if 'РЪКОВОДИТЕЛ НА КУРСОВЕ КЪМ ПЕДАГОГИЧЕСКИЯ ФАКУЛТЕТ' in report:
            issue(p,'dean_signature_role','В доклада на декана длъжността под подписа е означена като ръководител на курсовете. Оригиналът и историческите проекти се запазват до изрично потвърждение на корекцията.','Ръководител на курсовете','Декан на Педагогическия факултет')
        if Path(p.source.path).name[:2] in ('03','05'):
            issue(p,'period_ambiguous','Курсът е проведен през май 2025 г., а административната дата е 06.2025. Няма изрично посочен отчетен месец. За първоначално групиране е използван май.','Дата на документа: 06.2025','Потвърждение на отчетния период от администратора')
        closing=p.values.filter(kind='closing').first()
        if not closing: continue
        for line_number,line in enumerate(p.source.text.splitlines()):
            if line.startswith('ОСТАТЪК ЗА СЛЕДВАЩО ПЛАЩАНЕ:'):
                amount,currency,_=parse_amount(line)
                if eur(amount,currency)!=closing.converted_eur:
                    issue(p,f'report_closing_{line_number}','Докладът и план-сметката посочват различни крайни остатъци.',line,str(closing.original)+' '+closing.currency,closing)
    ids=['02','07','13','12','10','04','08','14','01','09']; previous=None
    for index in ids:
        p=Period.objects.filter(source__path__contains='/'+index+'_').first()
        if not p: continue
        if previous:
            prior=previous.values.get(kind='closing'); opening=p.values.filter(kind='opening').first()
            if opening and prior.converted_eur!=opening.converted_eur:
                issue(p,'carry_forward','Пренесеният остатък се различава от крайния остатък на предходния наличен отчет.',opening.raw,f'{prior.original} {prior.currency} (източник: {previous.title})',opening)
        previous=p

def analysis_markdown():
    lines=['# Анализ на историческите източници','',
           'Оригиналите са съхранени без промени. Импортът не създава участници, банкови операции, реални плащания или потвърдени финансови правила.',
           'Отчетните периоди са установени по съдържанието, имената и датите; двусмислията са в регистъра. Историческите отчети остават проекти за проверка.',
           'Сумите в тази таблица са оригинални. Преизчисленията и разликите са в базата и регистъра.', '',
           '| Период | Валута | Постъпления | Разходи | Остатък | Несъответствия |', '|---|---|---:|---:|---:|---:|']
    for p in Period.objects.filter(historical=True).order_by('end','pk'):
        vals={v.kind:v for v in p.values.all()}
        lines.append(f"| {p.title} | {vals['total'].currency} | {vals['total'].original} | {vals['costs'].original} | {vals['closing'].original} | {p.discrepancies.count()} |")
    lines+=['','## Регистър за проверка','','| Период / ред | Разлика | Оригинал | Предложение |','|---|---|---|---|']
    for d in Discrepancy.objects.select_related('period','row').order_by('period__end','pk'):
        lines.append(f'| {d.period.title} / {d.row.row if d.row else "текст"} | {d.description} | {d.original} | {d.proposed} |')
    lines+=['','Потвърждаването на предложение записва решение и основание; не презаписва първоначалните стойности. Изменени отчети се изготвят като отделна ревизия.',
            'Месечните филтри за комбинирани исторически периоди използват последния месец на периода; точни дати на отделни постъпления не са известни.',
            'Възнагражденията по получатели не доказват еднозначна връзка лектор–курс. Не е измислено разпределение по лектори.']
    return '\n'.join(lines)+'\n'
