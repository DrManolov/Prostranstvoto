"""Official Google OAuth2 and Forms REST API. No simulated forms or registrations."""
import secrets
import time
from django.utils import timezone
from urllib.parse import urlencode
import requests
from cryptography.fernet import Fernet,InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect
from django.views.decorators.http import require_POST
from .models import Course,GoogleConnection,Participant
from .services import admin,audit,financial_lock

SCOPES='https://www.googleapis.com/auth/forms.body https://www.googleapis.com/auth/forms.responses.readonly'
API='https://forms.googleapis.com/v1/forms/'

def configuration():
    if not all([settings.GOOGLE_CLIENT_ID,settings.GOOGLE_CLIENT_SECRET,settings.TOKEN_ENCRYPTION_KEY]):
        raise ValidationError('Добавете GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET и TOKEN_ENCRYPTION_KEY в защитената конфигурация на сървъра. Вижте docs/GOOGLE.md.')
    try: return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())
    except (ValueError,TypeError) as e: raise ValidationError('Невалиден ключ за шифроване на Google данни.') from e

def response_json(response):
    if not response.ok:
        raise ValidationError(f'Google API върна HTTP {response.status_code}. Проверете разрешенията, активирането на Forms API и свързания акаунт; операцията не е представена като успешна.')
    return response.json()

def token(user):
    f=configuration()
    connection=GoogleConnection.objects.filter(user=user).first()
    if not connection: raise ValidationError('Първо свържете Google акаунт от раздел „Google Forms“.')
    try: refresh=f.decrypt(connection.encrypted_refresh_token.encode()).decode()
    except InvalidToken as e: raise ValidationError('Ключът за шифроване е променен. Свържете акаунта отново.') from e
    r=requests.post('https://oauth2.googleapis.com/token',data={
        'client_id':settings.GOOGLE_CLIENT_ID,'client_secret':settings.GOOGLE_CLIENT_SECRET,
        'refresh_token':refresh,'grant_type':'refresh_token'},timeout=30)
    return response_json(r)['access_token']

def call(user,method,path,**kwargs):
    return response_json(requests.request(method,API+path,headers={'Authorization':'Bearer '+token(user)},timeout=30,**kwargs))

@login_required
@require_POST
def connect(request):
    admin(request.user)
    try: configuration()
    except ValidationError as e:
        messages.error(request,'; '.join(e.messages)); return redirect('integration')
    state=secrets.token_urlsafe(32)
    request.session['google_oauth']={'state':state,'at':time.time(),'user':request.user.pk}
    query=urlencode({'client_id':settings.GOOGLE_CLIENT_ID,'redirect_uri':settings.GOOGLE_REDIRECT_URI,
        'response_type':'code','scope':SCOPES,'access_type':'offline','prompt':'consent','state':state})
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?'+query)

@login_required
def callback(request):
    admin(request.user)
    pending=request.session.pop('google_oauth',{})
    valid=(pending.get('user')==request.user.pk and time.time()-pending.get('at',0)<600 and
           secrets.compare_digest(str(pending.get('state','')),str(request.GET.get('state',''))) and pending.get('state'))
    if not valid:
        messages.error(request,'Невалидна или изтекла OAuth заявка. Започнете свързването отново.'); return redirect('integration')
    if request.GET.get('error') or not request.GET.get('code'):
        messages.error(request,'Google достъпът не е разрешен.'); return redirect('integration')
    try:
        f=configuration()
        response=requests.post('https://oauth2.googleapis.com/token',data={
            'code':request.GET['code'],'client_id':settings.GOOGLE_CLIENT_ID,'client_secret':settings.GOOGLE_CLIENT_SECRET,
            'redirect_uri':settings.GOOGLE_REDIRECT_URI,'grant_type':'authorization_code'},timeout=30)
        data=response_json(response)
        if 'refresh_token' not in data: raise ValidationError('Google не върна дълготрайно разрешение. Отменете старото разрешение в Google и свържете отново.')
        conn,_=GoogleConnection.objects.update_or_create(user=request.user,defaults={
            'encrypted_refresh_token':f.encrypt(data['refresh_token'].encode()).decode()})
        audit(request.user,'Свързан Google акаунт',conn)
        messages.success(request,'Google акаунтът е свързан чрез OAuth2. Разрешението се съхранява шифровано.')
    except (ValidationError,requests.RequestException) as e:
        messages.error(request,'; '.join(e.messages) if isinstance(e,ValidationError) else 'Мрежова грешка при свързване с Google.')
    return redirect('integration')

@login_required
@require_POST
def disconnect(request):
    admin(request.user)
    conn=GoogleConnection.objects.filter(user=request.user).first()
    if conn:
        try:
            f=configuration(); refresh=f.decrypt(conn.encrypted_refresh_token.encode()).decode()
            response=requests.post('https://oauth2.googleapis.com/revoke',data={'token':refresh},timeout=30)
            if not response.ok: raise ValidationError('Google не потвърди отмяната. Можете да отмените достъпа от настройките на Google акаунта.')
        except (ValidationError,InvalidToken,requests.RequestException):
            messages.warning(request,'Локалното разрешение е изтрито. Проверете и отменете достъпа и в настройките за сигурност на Google акаунта.')
        conn.delete()
    messages.success(request,'Локалната връзка с Google е премахната.')
    return redirect('integration')

def create_form(course,user):
    if not settings.PRIVACY_NOTICE.strip(): raise ValidationError('Добавете одобрено от университета уведомление за личните данни в PRIVACY_NOTICE преди публикуване на форма.')
    if not course.start: raise ValidationError('Първо задайте дата и час на курса.')
    prices=list(course.prices.all())
    if not prices: raise ValidationError('Първо добавете поне една ценова категория.')
    # Keep the real form ID immediately after Google creates it, even if publication fails.
    if not course.form_id:
        data=call(user,'POST','',json={'info':{'title':course.title,'documentTitle':'ПФ+ — '+course.title}})
        course.form_id=data['formId']; course.form_url=data.get('responderUri',''); course.google_question_map={'owner':user.pk}; course.save()
    if course.google_question_map.get('owner')!=user.pk: raise ValidationError('Формата е създадена с друг Google акаунт. Използвайте същия администратор.')
    existing=call(user,'GET',course.form_id)
    labels=[('name','Име и фамилия',True),('email','Имейл',True),('phone','Телефон',True),
            ('price','Ценова категория',True),('invoice','Информация за фактура (ако е необходима)',False),
            ('privacy','Уведомление за обработване на личните данни',True)]
    if not existing.get('items'):
        requests_body=[{'updateFormInfo':{'info':{'description':f'{timezone.localtime(course.start):%d.%m.%Y %H:%M} • {course.get_format_display()}\n\n{settings.PRIVACY_NOTICE}'},'updateMask':'description'}}]
        for index,(key,title,required) in enumerate(labels):
            question={'required':required}
            if key=='price': question['choiceQuestion']={'type':'RADIO','options':[{'value':f'{p.name} — {p.amount} EUR'} for p in prices]}
            elif key=='privacy': question['choiceQuestion']={'type':'CHECKBOX','options':[{'value':'Запознат/а съм с уведомлението за обработване на личните данни.'}]}
            else: question['textQuestion']={'paragraph':key=='invoice'}
            requests_body.append({'createItem':{'item':{'title':title,'questionItem':{'question':question}},'location':{'index':index}}})
        call(user,'POST',course.form_id+':batchUpdate',json={'requests':requests_body})
        existing=call(user,'GET',course.form_id)
    mapping={'owner':user.pk}
    titles={title:key for key,title,_ in labels}
    for item in existing.get('items',[]):
        if item.get('title') in titles: mapping[titles[item['title']]]=item['questionItem']['question']['questionId']
    if len(mapping)!=7: raise ValidationError('Формата е променена в Google. Проверете задължителните полета преди синхронизация.')
    mapping['prices']={f'{p.name} — {p.amount} EUR':p.pk for p in prices}
    course.google_question_map=mapping; course.form_url=existing.get('responderUri',course.form_url); course.save()
    call(user,'POST',course.form_id+':setPublishSettings',json={'publishSettings':{'publishState':{'isPublished':True,'isAcceptingResponses':True}}})
    audit(user,'Създадена/публикувана Google форма',course,after={'form_id':course.form_id})
    return course.form_url

def sync_responses(course,user):
    if not course.form_id: raise ValidationError('За курса няма създадена Google форма.')
    mapping=course.google_question_map
    if mapping.get('owner')!=user.pk: raise ValidationError('Необходим е Google акаунтът, създал формата.')
    if not all(k in mapping for k in ['name','email','phone','price','invoice']): raise ValidationError('Първо довършете създаването на формата.')
    count=0; failures=0; page=None
    while True:
        result=call(user,'GET',course.form_id+'/responses',params={'pageSize':500,**({'pageToken':page} if page else {})})
        for response in result.get('responses',[]):
            response_id=course.form_id+':'+response['responseId']
            if Participant.objects.filter(google_response_id=response_id).exists(): continue
            answers=response.get('answers',{})
            def answer(key):
                values=answers.get(mapping[key],{}).get('textAnswers',{}).get('answers',[])
                return values[0].get('value','').strip() if values else ''
            price_id=mapping.get('prices',{}).get(answer('price'))
            price=course.prices.filter(pk=price_id).first()
            if not price: failures+=1; continue
            try:
                with transaction.atomic():
                    financial_lock(); Course.objects.select_for_update().get(pk=course.pk)
                    if Participant.objects.filter(course=course,email=answer('email').lower()).exists(): continue
                    if course.participants.exclude(state='cancelled').count()>=course.capacity: failures+=1; continue
                    p=Participant.objects.create(course=course,name=answer('name'),email=answer('email').lower(),phone=answer('phone'),
                        price=price,due=price.amount,due_date=timezone.localtime(course.start).date() if course.start else None,invoice_data=answer('invoice'),google_response_id=response_id)
                    audit(user,'Google Forms регистрация (без плащане)',p,after={'course_id':course.pk})
                    count+=1
            except ValidationError: failures+=1
        page=result.get('nextPageToken')
        if not page: break
    return count,failures

@login_required
@require_POST
def course_google(request,pk,action):
    admin(request.user); course=get_object_or_404(Course,pk=pk)
    try:
        if action=='create':
            create_form(course,request.user); messages.success(request,'Google потвърди публикуването на действителната форма. Линкът е в курса.')
        elif action=='sync':
            count,failures=sync_responses(course,request.user)
            messages.success(request,f'Нови регистрации: {count}. Изискват проверка: {failures}. Плащания не са създавани.')
        else: raise ValidationError('Непознато действие.')
    except (ValidationError,requests.RequestException) as e:
        messages.error(request,'; '.join(e.messages) if isinstance(e,ValidationError) else 'Мрежова грешка при заявката към Google. Проверете връзката и повторете.')
    return redirect('course',pk=pk)
