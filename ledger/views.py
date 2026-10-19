import csv
import hashlib
import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import FileResponse,Http404,HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import FORMS
from .models import (Audit,Course,Discrepancy,Expense,GeneratedDocument,GoogleConnection,HistoricalValue,
                     Lecturer,LoginAttempt,Participant,Payment,Period,SourceDocument,Transfer)
from .services import (admin,audit,budget,close_period,confirm_payment,dashboard_data,financial_lock,
                       open_date,pay_expense,period_totals,serial,writer)

class SecureLoginView(LoginView):
    template_name='registration/login.html'
    def post(self,request,*args,**kwargs):
        keys=[hashlib.sha256(('ip:'+request.META.get('REMOTE_ADDR','')).encode()).hexdigest(),
              hashlib.sha256(('user:'+request.POST.get('username','').lower()).encode()).hexdigest()]
        recent=timezone.now()-timedelta(minutes=15)
        if LoginAttempt.objects.filter(key__in=keys,failures__gte=10,last__gt=recent).exists():
            return HttpResponse('Твърде много опити. Опитайте отново след 15 минути.',status=429)
        response=super().post(request,*args,**kwargs)
        if response.status_code==302:
            LoginAttempt.objects.filter(key=keys[1]).delete()
        else:
            for key in keys:
                attempt,_=LoginAttempt.objects.get_or_create(key=key)
                attempt.failures=attempt.failures+1 if attempt.last>recent else 1
                attempt.save()
        return response

def filters_for(request):
    result={}
    for key in ['year','month','course','lecturer']:
        value=request.GET.get(key,'')
        if value.isdigit() and len(value)<6:
            n=int(value)
            if key=='month' and not 1<=n<=12: continue
            if key=='year' and not 1900<=n<=2200: continue
            result[key]=n
    if request.GET.get('kind') in ['tuition','recording','refund']: result['kind']=request.GET['kind']
    return result

def context(request,**kwargs):
    return dict(can_write=request.user.is_superuser or request.user.groups.filter(name='Счетоводител').exists(),**kwargs)

@login_required
def dashboard(request):
    filters=filters_for(request)
    data=dashboard_data(filters)
    data.update(filters=filters,courses=Course.objects.all(),lecturers=Lecturer.objects.all(),
                years=list(range(2025,timezone.now().year+3)),months=range(1,13),
                unresolved=Discrepancy.objects.filter(state='open').count(),
                recent_periods=Period.objects.all()[:5])
    return render(request,'ledger/dashboard.html',context(request,**data))

@login_required
def courses(request):
    qs=Course.objects.prefetch_related('lecturers','prices').all()
    if request.GET.get('q'): qs=qs.filter(title__icontains=request.GET['q'])
    if request.GET.get('status'): qs=qs.filter(status=request.GET['status'])
    return render(request,'ledger/courses.html',context(request,courses=qs.order_by('-pk')))

@login_required
def course_detail(request,pk):
    course=get_object_or_404(Course,pk=pk)
    data=dashboard_data({'course':pk})
    return render(request,'ledger/course.html',context(request,course=course,budget=budget(course),
         participants=course.participants.all(),history=HistoricalValue.objects.filter(course=course,kind='revenue').select_related('period'),**data))

@login_required
def analytics(request):
    filters=filters_for(request); data=dashboard_data(filters)
    data['course_stats'].sort(key=lambda c:c['historical']+c['actual'],reverse=True)
    return render(request,'ledger/analytics.html',context(request,filters=filters,**data))

@login_required
def create(request,entity,pk=None):
    if entity not in FORMS: raise Http404
    writer(request.user)
    form_class,label=FORMS[entity]
    instance=get_object_or_404(form_class._meta.model,pk=pk) if pk else None
    if instance and entity not in ['course','participant','expense','period']: raise PermissionDenied
    if entity=='period' and instance:
        admin(request.user)
        if instance.state!='draft': raise PermissionDenied('Първо отворете нова ревизия на утвърдения отчет.')
    initial={}
    if request.GET.get('course','').isdigit(): initial['course']=request.GET['course']
    form=form_class(request.POST or None,request.FILES or None,instance=instance,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                financial_lock()
                obj=form.save(commit=False)
                before=model_to_dict(form_class._meta.model.objects.get(pk=pk)) if pk else {}
                if hasattr(obj,'date'):
                    open_date(obj.date)
                    if instance: open_date(form_class._meta.model.objects.get(pk=pk).date)
                if entity=='expense' and instance and instance.disbursements.exists(): raise ValidationError('Има реални плащания. Създайте отделна корекция.')
                if entity=='period' and Period.objects.filter(state__in=['approved','locked'],end__gte=obj.start).exists():
                    raise ValidationError('Нов период не може да предшества вече утвърдени отчети.')
                if entity=='participant' and not instance:
                    course=Course.objects.select_for_update().get(pk=obj.course_id)
                    if course.participants.exclude(state='cancelled').count()>=course.capacity: raise ValidationError('Капацитетът на курса е достигнат.')
                if entity=='participant' and instance and instance.payments.exists():
                    # Re-read original; ModelForm has already mutated instance.
                    old=Participant.objects.get(pk=pk)
                    if old.course_id!=obj.course_id: raise ValidationError('Участник с плащания не може да се премества в друг курс.')
                if hasattr(obj,'created_by_id') and not pk: obj.created_by=request.user
                if entity=='disbursement': pay_expense(obj,request.user)
                else:
                    if entity=='period' and pk: obj.revision+=1
                    obj.save(); form.save_m2m()
                    audit(request.user,'Редакция' if pk else 'Създаване',obj,before,model_to_dict(obj),form.cleaned_data.get('change_reason',''))
            messages.success(request,'Записът е съхранен.')
            if entity=='course': return redirect('course',pk=obj.pk)
            if entity in ('participant','price'): return redirect('course',pk=obj.course_id)
            return redirect('register',section='periods' if entity=='period' else ('expenses' if entity in ['expense','disbursement'] else 'payments'))
        except (ValidationError,IntegrityError) as e:
            form.add_error(None,'; '.join(e.messages) if isinstance(e,ValidationError) else 'Дублирана референция или запис.')
    return render(request,'ledger/form.html',context(request,form=form,title=('Редакция на ' if pk else 'Нов запис: ')+label))

@login_required
def register(request,section):
    data={}
    if section=='payments': data['rows']=Payment.objects.select_related('course','participant').order_by('-date','-pk')
    elif section=='expenses': data['rows']=Expense.objects.select_related('course','rate').prefetch_related('disbursements').order_by('-date','-pk')
    elif section=='periods': data['rows']=Period.objects.select_related('source').all()
    elif section=='discrepancies':
        data['rows']=Discrepancy.objects.select_related('period','row').order_by('state','period__end','pk')
        if request.GET.get('state'): data['rows']=data['rows'].filter(state=request.GET['state'])
    elif section=='documents': data['rows']=GeneratedDocument.objects.select_related('period').order_by('-created_at')
    elif section=='audit':
        admin(request.user); data['rows']=Audit.objects.select_related('actor').all()[:300]
    elif section=='transfers': data['rows']=Transfer.objects.order_by('-date')
    elif section=='participants': data['rows']=Participant.objects.select_related('course').order_by('-pk')
    else: raise Http404
    data['rows']=filter_register(data['rows'],section,request)
    return render(request,'ledger/register.html',context(request,section=section,**data))

def filter_register(qs,section,request):
    f=filters_for(request)
    date_field={'periods':'end','discrepancies':'period__end','documents':'created_at','participants':'created_at','audit':'at'}.get(section,'date')
    if section=='audit': return qs
    for key in ['year','month']:
        if f.get(key): qs=qs.filter(**{date_field+'__'+key:f[key]})
    if section in ['payments','expenses','participants']:
        if f.get('course'): qs=qs.filter(course_id=f['course'])
        if f.get('lecturer'): qs=qs.filter(course__lecturers__pk=f['lecturer']).distinct()
    if section=='payments' and f.get('kind'): qs=qs.filter(kind=f['kind'])
    query=request.GET.get('q','').strip()[:200]
    searches={'payments':['payer','reference','course__title'],'expenses':['payee','notes','course__title'],
        'periods':['title'],'discrepancies':['description','period__title'],'documents':['period__title'],
        'participants':['name','email','course__title'],'transfers':['reference','destination']}
    if query and section in searches:
        cond=Q()
        for field in searches[section]: cond|=Q(**{field+'__icontains':query})
        qs=qs.filter(cond)
    return qs

@login_required
def period_detail(request,pk):
    p=get_object_or_404(Period,pk=pk)
    totals=p.snapshot if p.state!='draft' else period_totals(p)
    return render(request,'ledger/period.html',context(request,period=p,totals=totals,values=p.values.all(),
        discrepancies=p.discrepancies.all(),documents=p.documents.order_by('-created_at')))

@login_required
@require_POST
def payment_confirm(request,pk):
    try: confirm_payment(pk,request.user); messages.success(request,'Плащането е потвърдено и отчетено еднократно.')
    except ValidationError as e: messages.error(request,'; '.join(e.messages))
    return redirect('register',section='payments')

@login_required
@require_POST
def period_action(request,pk,action):
    try: close_period(pk,request.user,action,request.POST.get('reason','')); messages.success(request,'Статусът на отчета е обновен.')
    except ValidationError as e: messages.error(request,'; '.join(e.messages))
    return redirect('period',pk=pk)

@login_required
@require_POST
def review_discrepancy(request,pk):
    admin(request.user)
    with transaction.atomic():
        d=get_object_or_404(Discrepancy.objects.select_for_update(),pk=pk)
        reason=request.POST.get('reason','').strip(); decision=request.POST.get('decision')
        if reason and decision in ('accepted','rejected') and d.state=='open':
            before=model_to_dict(d); d.state=decision; d.reason=reason; d.reviewed_by=request.user; d.reviewed_at=timezone.now(); d.save()
            if decision=='accepted' and d.code=='dean_signature_role':
                p=Period.objects.select_for_update().get(pk=d.period_id)
                p.revision+=1; p.save()
            audit(request.user,'Решение за несъответствие',d,before,model_to_dict(d),reason)
            messages.success(request,'Решението е записано. Оригиналните стойности са запазени; коригирана отчетна стойност се въвежда в отделна ревизия.')
        else: messages.error(request,'Необходими са основание и решение за неразгледано несъответствие.')
    return redirect('register',section='discrepancies')

@login_required
def source_download(request,pk):
    source=get_object_or_404(SourceDocument,pk=pk)
    path=(settings.BASE_DIR/source.path).resolve()
    if not path.is_relative_to((settings.BASE_DIR/'data'/'originals').resolve()): raise Http404
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source.sha256: raise ValidationError('Оригиналът не съвпада с контролната сума.')
    return FileResponse(path.open('rb'),as_attachment=True,filename=source.filename)

@login_required
def attachment(request,pk):
    payment=get_object_or_404(Payment,pk=pk)
    if not payment.attachment: raise Http404
    return FileResponse(payment.attachment.open('rb'),as_attachment=True,filename=Path(payment.attachment.name).name)

def safe_cell(value):
    if isinstance(value,(Decimal,int,bool)): return value
    value=str(value)
    return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value

def tabular_response(rows,kind,filename):
    rows=[[safe_cell(x) for x in row] for row in rows]
    if kind=='csv':
        response=HttpResponse(content_type='text/csv; charset=utf-8-sig'); response.write('\ufeff')
        response['Content-Disposition']=f'attachment; filename="{filename}.csv"'
        csv.writer(response).writerows(rows); return response
    if kind!='xlsx': raise Http404
    from openpyxl import Workbook
    wb=Workbook(); sheet=wb.active; sheet.title='ПФ+'
    for row in rows: sheet.append(row)
    sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
    for col in sheet.columns: sheet.column_dimensions[col[0].column_letter].width=min(80,max(len(str(c.value or '')) for c in col)+3)
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return FileResponse(out,as_attachment=True,filename=filename+'.xlsx')

@login_required
def register_export(request,section,kind):
    models={'payments':Payment,'expenses':Expense,'periods':Period,'discrepancies':Discrepancy,
            'participants':Participant,'transfers':Transfer,'documents':GeneratedDocument}
    if section not in models: raise Http404
    qs=filter_register(models[section].objects.all(),section,request)
    if section=='payments':
        rows=[['Дата','Курс','Платец','Вид','Оригинална сума','Валута','Сума EUR','Разлика от конвертиране EUR','Потвърдено','Референция']]
        rows += [[r.date,r.course,r.payer,r.get_kind_display(),r.amount,r.currency,r.signed_eur,r.conversion_rounding,r.confirmed,r.reference] for r in qs]
    elif section=='expenses':
        rows=[['Дата','Курс','Получател','Категория','Сума EUR','Осигуровки EUR','Общо EUR','Статус','Платено EUR']]
        rows += [[r.date,r.course or 'Обща дейност',r.payee,r.get_category_display(),r.amount,r.insurance,r.total,r.get_state_display(),r.paid] for r in qs]
    elif section=='periods':
        rows=[['Период','От','До','Статус','Исторически','Ревизия','Документален остатък EUR']]
        rows += [[r.title,r.start,r.end,r.get_state_display(),r.historical,r.revision,period_totals(r).get('closing','')] for r in qs]
    elif section=='discrepancies':
        rows=[['Период','Код','Описание','Оригинал','Предложение','Решение','Основание']]
        rows += [[r.period,r.code,r.description,r.original,r.proposed,r.get_state_display(),r.reason] for r in qs]
    elif section=='participants':
        rows=[['Участник','Курс','Имейл','Телефон','Дължимо EUR','Платено EUR','Статус']]
        rows += [[str(r),r.course,'' if r.restricted else r.email,'' if r.restricted else r.phone,r.due,r.paid,r.payment_status] for r in qs]
    elif section=='transfers':
        rows=[['Дата','Структура','Сума EUR','Референция']]
        rows += [[r.date,r.get_destination_display(),r.amount,r.reference] for r in qs]
    else:
        rows=[['Период','Дата','Статус','Утвърждаване','Автор']]
        rows += [[r.period,r.created_at,r.get_status_display(),r.approval_reference,r.created_by] for r in qs]
    return tabular_response(rows,kind,'pfplus-'+section)

@login_required
def export(request,kind):
    data=dashboard_data(filters_for(request))
    rows=[['Произход','Период / дата','Курс','Сума EUR','Референция / източник']]
    rows += [['Исторически агрегат',v.period.title,str(v.course),str(v.converted_eur),v.period.source.filename] for v in data['historical_rows']]
    rows += [['Потвърдено плащане',str(p.date),str(p.course),str(p.signed_eur),p.reference] for p in data['payments']]
    rows=[[safe_cell(x) for x in row] for row in rows]
    if kind=='csv':
        response=HttpResponse(content_type='text/csv; charset=utf-8-sig'); response.write('\ufeff')
        response['Content-Disposition']='attachment; filename="pfplus-finance.csv"'
        csv.writer(response).writerows(rows); return response
    if kind!='xlsx': raise Http404
    from openpyxl import Workbook
    wb=Workbook(); sheet=wb.active; sheet.title='Постъпления'
    for row in rows: sheet.append(row)
    sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
    for col in sheet.columns: sheet.column_dimensions[col[0].column_letter].width=min(80,max(len(str(c.value or '')) for c in col)+3)
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return FileResponse(out,as_attachment=True,filename='pfplus-finance.xlsx')

@login_required
def integration(request):
    return render(request,'ledger/google.html',context(request,connected=GoogleConnection.objects.filter(user=request.user).exists(),
        configured=all([settings.GOOGLE_CLIENT_ID,settings.GOOGLE_CLIENT_SECRET,settings.TOKEN_ENCRYPTION_KEY]),privacy=bool(settings.PRIVACY_NOTICE)))

@login_required
@require_POST
def generate_documents(request,pk):
    from datetime import date
    from .documents import generate
    period=get_object_or_404(Period,pk=pk)
    try:
        day=date.fromisoformat(request.POST.get('date') or str(timezone.localdate()))
        doc=generate(period,request.user,day)
        if doc.data.get('pdf_errors'): messages.warning(request,'DOCX файловете са създадени. '+'; '.join(doc.data['pdf_errors']))
        else: messages.success(request,'Създадени са DOCX и PDF проекти по оригиналния шаблон.')
        return redirect('document',pk=doc.pk)
    except (ValidationError,ValueError) as e:
        messages.error(request,'; '.join(e.messages) if isinstance(e,ValidationError) else 'Невалидна дата.')
    return redirect('period',pk=pk)

@login_required
def document_detail(request,pk):
    from .documents import NAMES
    doc=get_object_or_404(GeneratedDocument.objects.select_related('period'),pk=pk)
    links=[{'filename':name,'label':NAMES.get(name.split('.')[0],'Източници на сумите')+' · '+name.split('.')[-1].upper()} for name in doc.files]
    return render(request,'ledger/document.html',context(request,doc=doc,file_links=links))

@login_required
def document_file(request,pk,name):
    doc=get_object_or_404(GeneratedDocument,pk=pk)
    if name not in doc.files: raise Http404
    path=(settings.MEDIA_ROOT/doc.files[name]).resolve()
    if not path.is_relative_to(settings.MEDIA_ROOT.resolve()) or not path.is_file(): raise Http404
    expected=doc.data.get('file_hashes',{}).get(name)
    if expected and hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise ValidationError('Файлът е променен след генерирането.')
    inline=request.GET.get('preview')=='1' and name.endswith('.pdf')
    response=FileResponse(path.open('rb'),as_attachment=not inline,filename=name)
    response['Cache-Control']='private, no-store'
    if inline: response['X-Frame-Options']='SAMEORIGIN'
    return response

@login_required
@require_POST
def approve_document(request,pk):
    admin(request.user)
    doc=get_object_or_404(GeneratedDocument,pk=pk)
    reference=request.POST.get('reference','').strip()
    if not reference or doc.status!='draft' or doc.period.state not in ('approved','locked') or doc.data.get('period_revision')!=doc.period.revision:
        messages.error(request,'Необходимо е основание за действително утвърждаване и утвърден отчет със същата ревизия.')
    else:
        doc.status='approved'; doc.approval_reference=reference; doc.approved_by=request.user; doc.approved_at=timezone.now(); doc.save()
        audit(request.user,'Регистрирано действително утвърждаване на документа',doc,after={'reference':reference},reason=reference)
        messages.success(request,'Утвърждаването е регистрирано. Генерираните файлове са запазените проекти; подписаният оригинал се съхранява по университетския ред.')
    return redirect('document',pk=pk)

@login_required
@require_POST
def adjust_history(request,pk):
    from .models import HistoricalAdjustment
    from .money import money
    from decimal import InvalidOperation
    admin(request.user)
    row=get_object_or_404(HistoricalValue,pk=pk)
    try:
        with transaction.atomic():
            financial_lock(); p=Period.objects.select_for_update().get(pk=row.period_id)
            if p.state!='draft': raise ValidationError('Първо отворете нова ревизия на утвърдения отчет.')
            reason=request.POST.get('reason','').strip()
            if not reason: raise ValidationError('Необходимо е основание за корекцията.')
            value=money(request.POST.get('amount','').replace(',','.'))
            before={'effective_eur':row.effective_eur,'revision':p.revision}
            p.revision+=1; p.save()
            change=HistoricalAdjustment.objects.create(row=row,amount=value,reason=reason,created_by=request.user,revision=p.revision)
            audit(request.user,'Историческа корекция в отделна ревизия',change,before,{'amount':value,'currency':row.currency,'source_row':row.pk},reason)
            messages.success(request,'Корекцията е запазена в нова ревизия. Оригиналният ред е непроменен. Свързаните редове не се изменят автоматично.')
    except (ValidationError,InvalidOperation) as e:
        messages.error(request,'; '.join(e.messages) if isinstance(e,ValidationError) else 'Невалидна сума.')
    return redirect('period',pk=row.period_id)
