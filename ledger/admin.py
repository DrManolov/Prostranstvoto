from django.contrib import admin
from django.db import transaction
from django.forms.models import model_to_dict
from . import models
from .services import audit, financial_lock

admin.site.site_header='ПФ+ · Административни настройки'
admin.site.site_title='Пространството ПФ+'
admin.site.index_title='Финансови правила, ставки и длъжностни лица'

class ConfigurationAdmin(admin.ModelAdmin):
    def has_module_permission(self,request): return request.user.is_superuser
    def has_add_permission(self,request): return request.user.is_superuser
    def has_change_permission(self,request,obj=None): return request.user.is_superuser
    def has_delete_permission(self,request,obj=None): return False
    def save_model(self,request,obj,form,change):
        with transaction.atomic():
            financial_lock()
            before=model_to_dict(type(obj).objects.get(pk=obj.pk)) if change else {}
            obj.save()
            audit(request.user,'Административна настройка',obj,before,model_to_dict(obj))

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self,request): return request.user.is_superuser
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False

@admin.register(models.FinancialRule)
class RuleAdmin(ConfigurationAdmin):
    list_display=['name','course','valid_from','valid_until','university','faculty','center','confirmed']

@admin.register(models.CompensationRate)
class RateAdmin(ConfigurationAdmin):
    list_display=['name','category','percent','valid_from','valid_until','confirmed']

@admin.register(models.Official)
class OfficialAdmin(ConfigurationAdmin):
    list_display=['role','name','valid_from','valid_until','confirmed']

admin.site.register(models.Lecturer,ConfigurationAdmin)
for model in [models.Course,models.Participant,models.Payment,models.Expense,models.Disbursement,
              models.Transfer,models.Period,models.SourceDocument,models.Discrepancy,models.Audit]:
    admin.site.register(model,ReadOnlyAdmin)
