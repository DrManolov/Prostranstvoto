import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.db import models,transaction,IntegrityError
from django.db.models import Q
from .money import ZERO, eur,conversion_delta

CURRENCY = [('EUR', 'EUR'), ('BGN', 'BGN')]
positive = [MinValueValidator(Decimal('0.01'))]

class Validated(models.Model):
    class Meta:
        abstract = True
    def save(self, *args, **kwargs):
        self.clean()
        self.full_clean()
        return super().save(*args, **kwargs)

class FinancialReference(models.Model):
    reference = models.CharField(max_length=200,primary_key=True)
    record_type = models.CharField(max_length=100)

class FinancialEntry(Validated):
    class Meta: abstract=True
    def save(self,*args,**kwargs):
        self.reference=self.reference.strip()
        try:
            with transaction.atomic():
                if not self.pk: FinancialReference.objects.create(reference=self.reference,record_type=self._meta.label)
                elif type(self).objects.get(pk=self.pk).reference!=self.reference: raise ValidationError('Платежната референция не се променя.')
                return super().save(*args,**kwargs)
        except IntegrityError as e:
            raise ValidationError('Тази платежна референция вече е използвана в системата.') from e

class Lecturer(Validated):
    name = models.CharField('Име и длъжност', max_length=240, unique=True)
    category = models.CharField('Категория', max_length=20, choices=[('staff','Щатен'),('external','Външен'),('pensioner','Пенсионер')], default='external')
    def __str__(self): return self.name
    class Meta:
        verbose_name = 'Лектор'; verbose_name_plural = 'Лектори'

class Course(Validated):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField('Заглавие', max_length=500)
    description = models.TextField('Описание', blank=True)
    lecturers = models.ManyToManyField(Lecturer, verbose_name='Лектори', blank=True)
    start = models.DateTimeField('Начало', null=True, blank=True)
    duration_hours = models.DecimalField('Продължителност (часове)', max_digits=7, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    format = models.CharField('Формат', max_length=20, default='online', choices=[('onsite','Присъствен'),('online','Онлайн'),('hybrid','Хибриден'),('recording','Запис')])
    capacity = models.PositiveIntegerField('Максимален брой участници', default=100)
    status = models.CharField('Статус', max_length=20, default='draft', choices=[('draft','Проект'),('open','Отворен за записване'),('completed','Приключил'),('cancelled','Отменен'),('archived','Архивиран')])
    expected_count = models.PositiveIntegerField('Планирани участници', default=0)
    expected_price = models.DecimalField('Планирана цена EUR', max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    expected_cost = models.DecimalField('Планирани общи разходи EUR', max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    form_id = models.CharField(max_length=200, blank=True, editable=False)
    form_url = models.URLField(blank=True, editable=False)
    google_question_map = models.JSONField(default=dict, blank=True, editable=False)
    def __str__(self): return self.title
    class Meta:
        verbose_name = 'Курс'; verbose_name_plural = 'Курсове'

class PriceCategory(Validated):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='prices', verbose_name='Курс')
    name = models.CharField('Категория', max_length=100)
    amount = models.DecimalField('Цена EUR', max_digits=12, decimal_places=2, validators=positive)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['course','name'], name='unique_course_price')]
        verbose_name = 'Ценова категория'; verbose_name_plural = 'Ценови категории'
    def __str__(self): return f'{self.name} — {self.amount} €'

class Participant(Validated):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='participants', verbose_name='Курс')
    name = models.CharField('Име и фамилия', max_length=200)
    email = models.EmailField('Имейл')
    phone = models.CharField('Телефон', max_length=50, blank=True)
    price = models.ForeignKey(PriceCategory, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Ценова категория')
    due = models.DecimalField('Дължима сума EUR', max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    due_date = models.DateField('Очаквана дата на плащане',null=True,blank=True)
    invoice_data = models.TextField('Данни за фактура', blank=True)
    state = models.CharField('Регистрация', max_length=20, default='registered', choices=[('registered','Записан'),('cancelled','Отказал се')])
    google_response_id = models.CharField(max_length=200, blank=True, null=True, unique=True, editable=False)
    restricted = models.BooleanField('Ограничено обработване', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def clean(self):
        self.email = self.email.lower().strip()
        if self.price and self.price.course_id != self.course_id: raise ValidationError('Ценовата категория е за друг курс.')
    @property
    def paid(self):
        return sum((p.signed_eur for p in self.payments.filter(confirmed=True)), ZERO)
    @property
    def payment_status(self):
        if self.state == 'cancelled': return 'Отказал се'
        if self.payments.filter(confirmed=True,kind='refund').exists() and self.paid <= 0: return 'Възстановена сума'
        if self.paid >= self.due: return 'Платил'
        if self.paid > 0: return 'Частично платил'
        return 'Очаква плащане'
    def __str__(self): return self.name if not self.restricted else 'Ограничени лични данни'
    class Meta:
        constraints = [models.UniqueConstraint(fields=['course','email'], name='unique_course_email')]
        verbose_name = 'Участник'; verbose_name_plural = 'Участници'

class Effective(Validated):
    valid_from = models.DateField('В сила от')
    valid_until = models.DateField('В сила до', null=True, blank=True)
    accounting_reference = models.CharField('Потвърждение от счетоводството', max_length=300, blank=True)
    confirmed = models.BooleanField('Потвърдено от счетоводството', default=False)
    class Meta: abstract = True
    def clean(self):
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from: raise ValidationError('Краят е преди началото.')
        if self.confirmed and not self.accounting_reference.strip(): raise ValidationError('Необходимо е основание от счетоводството.')

class FinancialRule(Effective):
    name = models.CharField('Наименование', max_length=200)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Само за курс')
    university = models.DecimalField('Университет %', max_digits=6, decimal_places=3, validators=[MinValueValidator(0),MaxValueValidator(100)])
    faculty = models.DecimalField('Факултет %', max_digits=6, decimal_places=3, validators=[MinValueValidator(0),MaxValueValidator(100)])
    center = models.DecimalField('ПФ+ %', max_digits=6, decimal_places=3, validators=[MinValueValidator(0),MaxValueValidator(100)])
    def clean(self):
        super().clean()
        if sum((self.university or ZERO, self.faculty or ZERO, self.center or ZERO)) > 100: raise ValidationError('Процентите надвишават 100%.')
        if self.confirmed and self.valid_from:
            qs = FinancialRule.objects.filter(confirmed=True, course=self.course).exclude(pk=self.pk)
            qs = qs.filter(Q(valid_until__isnull=True)|Q(valid_until__gte=self.valid_from))
            if self.valid_until: qs = qs.filter(valid_from__lte=self.valid_until)
            if qs.exists(): raise ValidationError('Застъпване с потвърдено финансово правило.')
        if self.pk and Payment.objects.filter(rule_id=self.pk, confirmed=True).exists():
            old = FinancialRule.objects.get(pk=self.pk)
            if any(getattr(old,f)!=getattr(self,f) for f in ['university','faculty','center','course_id','valid_from','confirmed']):
                raise ValidationError('Използвано правило не се променя. Създайте нов период на действие.')
            if self.valid_until and Payment.objects.filter(rule_id=self.pk,confirmed=True,date__gt=self.valid_until).exclude(kind='refund').exists():
                raise ValidationError('Крайната дата изключва вече потвърдено плащане.')
    def __str__(self): return self.name
    class Meta:
        verbose_name = 'Финансово правило'; verbose_name_plural = 'Финансови правила'

class CompensationRate(Effective):
    name = models.CharField('Категория възнаграждение', max_length=200)
    category = models.CharField('Вид', max_length=20, choices=[('staff','Щатен'),('external','Външен'),('pensioner','Пенсионер')])
    percent = models.DecimalField('Осигуровки %', max_digits=6, decimal_places=3, validators=[MinValueValidator(0),MaxValueValidator(100)])
    def clean(self):
        super().clean()
        if self.pk and Expense.objects.filter(rate_id=self.pk).exists():
            old=CompensationRate.objects.get(pk=self.pk)
            if any(getattr(old,k)!=getattr(self,k) for k in ['percent','category','valid_from','confirmed']):
                raise ValidationError('Използвана ставка не се променя. Добавете ставка с нов период.')
            if self.valid_until and Expense.objects.filter(rate_id=self.pk,date__gt=self.valid_until).exists():
                raise ValidationError('Крайната дата изключва вече записан разход.')
    def __str__(self): return f'{self.name} ({self.percent}%)'
    class Meta:
        verbose_name = 'Осигурителна ставка'; verbose_name_plural = 'Осигурителни ставки'

class Official(Effective):
    role = models.CharField('Длъжност', max_length=20, choices=[('rector','Ректор'),('dean','Декан'),('leader','Ръководител'),('accountant','Главен счетоводител')])
    name = models.CharField('Име и титла', max_length=250)
    def __str__(self): return self.name
    class Meta:
        verbose_name = 'Длъжностно лице'; verbose_name_plural = 'Длъжностни лица'

class SourceDocument(Validated):
    filename = models.CharField(max_length=500)
    path = models.CharField(max_length=600)
    sha256 = models.CharField(max_length=64, unique=True)
    text = models.TextField()
    imported_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.filename
    class Meta:
        verbose_name = 'Оригинален документ'; verbose_name_plural = 'Оригинални документи'

class Period(Validated):
    title = models.CharField('Отчетен период', max_length=200)
    start = models.DateField('От')
    end = models.DateField('До')
    historical = models.BooleanField(default=False, editable=False)
    source = models.OneToOneField(SourceDocument, on_delete=models.PROTECT, null=True, blank=True, editable=False)
    state = models.CharField(max_length=20, default='draft', choices=[('draft','Проект'),('approved','Утвърден'),('locked','Заключен')], editable=False)
    snapshot = models.JSONField(default=dict, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    def clean(self):
        if self.start and self.end and self.end < self.start: raise ValidationError('Невалиден период.')
        if not self.historical and self.start and self.end:
            qs = Period.objects.filter(historical=False,start__lte=self.end,end__gte=self.start).exclude(pk=self.pk)
            if qs.exists(): raise ValidationError('Отчетните периоди не могат да се застъпват.')
    def __str__(self): return self.title
    class Meta:
        ordering = ['-end','-pk']; verbose_name = 'Отчетен период'; verbose_name_plural = 'Отчетни периоди'

class HistoricalValue(Validated):
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name='values')
    row = models.PositiveIntegerField()
    label = models.TextField()
    raw = models.TextField(blank=True)
    kind = models.CharField(max_length=30)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, null=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY)
    original = models.DecimalField(max_digits=16, decimal_places=2)
    reported_eur = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    converted_eur = models.DecimalField(max_digits=16, decimal_places=2)
    recomputed_original = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    conversion_rounding = models.DecimalField(max_digits=20, decimal_places=10, default=0)
    metadata = models.JSONField(default=dict, blank=True)
    @property
    def effective_eur(self):
        adjustment=self.adjustments.order_by('-pk').first()
        return eur(adjustment.amount,self.currency) if adjustment else (self.reported_eur if self.reported_eur is not None else self.converted_eur)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['period','row'],name='unique_source_row')]
        ordering = ['row']

class HistoricalAdjustment(Validated):
    row = models.ForeignKey(HistoricalValue,on_delete=models.PROTECT,related_name='adjustments')
    amount = models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(0)])
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    revision = models.PositiveIntegerField()

class Discrepancy(Validated):
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name='discrepancies')
    row = models.ForeignKey(HistoricalValue, on_delete=models.PROTECT, null=True, blank=True)
    code = models.CharField(max_length=100)
    description = models.TextField()
    original = models.CharField(max_length=300, blank=True)
    proposed = models.CharField(max_length=300, blank=True)
    state = models.CharField(max_length=20, default='open', choices=[('open','За проверка'),('accepted','Потвърдена корекция'),('rejected','Запазена отчетена стойност')])
    reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    reviewed_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['period','code'],name='unique_discrepancy')]
        verbose_name = 'Несъответствие'; verbose_name_plural = 'Открити несъответствия'

class Payment(FinancialEntry):
    date = models.DateField('Дата на постъпване')
    course = models.ForeignKey(Course,on_delete=models.PROTECT,related_name='payments',verbose_name='Курс')
    participant = models.ForeignKey(Participant,on_delete=models.PROTECT,null=True,blank=True,related_name='payments',verbose_name='Участник')
    payer = models.CharField('Платец / организация',max_length=250)
    kind = models.CharField('Вид приход',max_length=20,choices=[('tuition','Такса'),('recording','Запис'),('refund','Възстановяване')],default='tuition')
    amount = models.DecimalField('Оригинална сума',max_digits=14,decimal_places=2,validators=positive)
    currency = models.CharField('Валута',max_length=3,choices=CURRENCY,default='EUR')
    amount_eur = models.DecimalField(max_digits=14,decimal_places=2,editable=False)
    conversion_rounding = models.DecimalField(max_digits=20,decimal_places=10,default=0,editable=False)
    method = models.CharField('Метод',max_length=30,choices=[('bank','Банков превод'),('cash','Каса'),('card','Карта')],default='bank')
    reference = models.CharField('Уникална платежна референция',max_length=200,unique=True)
    notes = models.TextField('Бележки',blank=True)
    attachment = models.FileField('Платежен документ (PDF/PNG/JPG)',upload_to='payments/%Y/%m',blank=True,validators=[FileExtensionValidator(['pdf','png','jpg','jpeg'])])
    confirmed = models.BooleanField(default=False,editable=False)
    rule = models.ForeignKey(FinancialRule,on_delete=models.PROTECT,null=True,blank=True,editable=False)
    allocation = models.JSONField(default=dict,blank=True,editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    refund_of = models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Възстановява плащане')
    @property
    def signed_eur(self): return -self.amount_eur if self.kind=='refund' else self.amount_eur
    def clean(self):
        self.reference=self.reference.strip()
        self.amount_eur=eur(self.amount or ZERO,self.currency)
        self.conversion_rounding=conversion_delta(self.amount or ZERO,self.currency).quantize(Decimal('0.0000000001'))
        if self.participant_id and self.participant.course_id!=self.course_id: raise ValidationError('Участникът е за друг курс.')
        if self.kind=='refund':
            if not self.refund_of_id: raise ValidationError('Изберете оригиналното плащане.')
            original=Payment.objects.get(pk=self.refund_of_id)
            if original.kind=='refund' or original.course_id!=self.course_id or not original.confirmed: raise ValidationError('Невалидно оригинално плащане.')
        elif self.refund_of_id: raise ValidationError('Само възстановяване може да сочи оригинално плащане.')
        if self.pk and Payment.objects.get(pk=self.pk).confirmed: raise ValidationError('Потвърдените плащания са неизменими. Използвайте възстановяване.')
    class Meta:
        verbose_name='Приход'; verbose_name_plural='Постъпления'

class Expense(Validated):
    date = models.DateField('Отчетна дата')
    course = models.ForeignKey(Course,on_delete=models.PROTECT,null=True,blank=True,verbose_name='Курс (или обща дейност)')
    payee = models.CharField('Получател / организация',max_length=250)
    category = models.CharField('Категория',max_length=20,choices=[('staff','Щатен служител'),('external','Външен лектор'),('pensioner','Хонорар пенсионер'),('insurance','Осигуровки'),('invoice','Фактура'),('other','Друг')])
    amount = models.DecimalField('Основна сума EUR',max_digits=14,decimal_places=2,validators=positive)
    rate = models.ForeignKey(CompensationRate,on_delete=models.PROTECT,null=True,blank=True,verbose_name='Потвърдена осигурителна ставка')
    insurance = models.DecimalField('Осигуровки EUR',max_digits=14,decimal_places=2,default=0,editable=False)
    planned_total = models.DecimalField('Планова обща сума EUR',max_digits=14,decimal_places=2,null=True,blank=True,editable=False)
    state = models.CharField('Състояние',max_length=20,default='planned',choices=[('planned','Предвиден'),('approved','Одобрен'),('due','Дължим')])
    notes = models.TextField('Основание / фактура',blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,editable=False)
    def clean(self):
        from .money import money
        if self.rate:
            if self.category!=self.rate.category or not self.rate.confirmed or not self.date or self.date<self.rate.valid_from or (self.rate.valid_until and self.date>self.rate.valid_until):
                raise ValidationError('Неприложима или непотвърдена осигурителна ставка.')
            self.insurance=money((self.amount or ZERO)*self.rate.percent/100)
        else: self.insurance=ZERO
        if self.state=='planned': self.planned_total=(self.amount or ZERO)+self.insurance
    @property
    def total(self): return self.amount+self.insurance
    @property
    def paid(self): return sum((x.amount for x in self.disbursements.all()),ZERO)
    class Meta:
        verbose_name='Разход'; verbose_name_plural='Разходи'

class Disbursement(FinancialEntry):
    expense = models.ForeignKey(Expense,on_delete=models.PROTECT,related_name='disbursements',verbose_name='Разход')
    date = models.DateField('Дата на плащане')
    amount = models.DecimalField('Платено EUR',max_digits=14,decimal_places=2,validators=positive)
    reference = models.CharField('Платежна референция',max_length=200,unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,editable=False)
    class Meta:
        verbose_name='Платен разход'; verbose_name_plural='Реални плащания по разходи'

class Transfer(FinancialEntry):
    date = models.DateField('Дата на превод')
    destination = models.CharField('Получател',max_length=20,choices=[('university','Университет'),('faculty','Факултет'),('center','ПФ+')])
    amount = models.DecimalField('Сума EUR',max_digits=14,decimal_places=2,validators=positive)
    reference = models.CharField('Платежна референция',max_length=200,unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,editable=False)
    class Meta:
        verbose_name='Превод на отчисления'; verbose_name_plural='Реални преводи на отчисления'

class Audit(models.Model):
    at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=200)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    class Meta:
        ordering=['-at']; verbose_name='Промяна'; verbose_name_plural='История на промените'

class GeneratedDocument(models.Model):
    period = models.ForeignKey(Period,on_delete=models.PROTECT,related_name='documents')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    data = models.JSONField()
    files = models.JSONField(default=dict)
    status = models.CharField(max_length=20,default='draft',choices=[('draft','Проект'),('approved','Утвърден')])
    approval_reference = models.CharField(max_length=300,blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,related_name='+')
    approved_at = models.DateTimeField(null=True)

class GoogleConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    encrypted_refresh_token = models.TextField()
    connected_at = models.DateTimeField(auto_now_add=True)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64,unique=True)
    failures = models.PositiveIntegerField(default=0)
    last = models.DateTimeField(auto_now=True)
