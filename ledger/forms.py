from django import forms
from .models import Course, Disbursement, Expense, Participant, Payment, Period, PriceCategory, Transfer

class StyledForm(forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for name,field in self.fields.items():
            field.widget.attrs['class']='input'
            if name=='date' or name in ['valid_from','valid_until','end','due_date']:
                field.widget=forms.DateInput(attrs={'type':'date','class':'input'},format='%Y-%m-%d')
            if name=='start' and self._meta.model==Period:
                field.widget=forms.DateInput(attrs={'type':'date','class':'input'},format='%Y-%m-%d')

class CourseForm(StyledForm):
    class Meta:
        model=Course
        fields=['title','description','lecturers','start','duration_hours','format','capacity','status','expected_count','expected_price','expected_cost']
        widgets={'start':forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M')}

class ParticipantForm(StyledForm):
    class Meta:
        model=Participant
        fields=['course','name','email','phone','price','due','due_date','invoice_data','state','restricted']

class PaymentForm(StyledForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['refund_of'].queryset=Payment.objects.filter(confirmed=True).exclude(kind='refund')
    class Meta:
        model=Payment
        fields=['date','course','participant','payer','kind','amount','currency','method','reference','notes','attachment','refund_of']
    def clean_attachment(self):
        f=self.cleaned_data.get('attachment')
        if f and f.size>10*1024*1024: raise forms.ValidationError('Максимален размер: 10 MB.')
        return f

class ExpenseForm(StyledForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from .models import CompensationRate
        self.fields['rate'].queryset=CompensationRate.objects.filter(confirmed=True)
    class Meta:
        model=Expense
        fields=['date','course','payee','category','amount','rate','state','notes']

class DisbursementForm(StyledForm):
    class Meta:
        model=Disbursement
        fields=['expense','date','amount','reference']

class TransferForm(StyledForm):
    class Meta:
        model=Transfer
        fields=['date','destination','amount','reference']

class PeriodForm(StyledForm):
    change_reason = forms.CharField(label='Основание за промяна (задължително при редакция)',required=False)
    def clean(self):
        data=super().clean()
        if self.instance.pk and not data.get('change_reason','').strip(): raise forms.ValidationError('Необходимо е основание за промяна на отчетния период.')
        return data
    class Meta:
        model=Period
        fields=['title','start','end']

class PriceForm(StyledForm):
    class Meta:
        model=PriceCategory
        fields=['course','name','amount']

FORMS={'course':(CourseForm,'курс'), 'participant':(ParticipantForm,'участник'),
       'payment':(PaymentForm,'постъпление'), 'expense':(ExpenseForm,'разход'),
       'disbursement':(DisbursementForm,'реално плащане по разход'), 'transfer':(TransferForm,'превод на отчисления'),
       'period':(PeriodForm,'отчетен период'),'price':(PriceForm,'ценова категория')}
