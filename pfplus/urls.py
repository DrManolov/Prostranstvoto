from django.contrib import admin
from django.contrib.auth.views import LogoutView,PasswordChangeView,PasswordChangeDoneView
from django.urls import path
from ledger import views,google

urlpatterns=[
 path('admin/',admin.site.urls),path('login/',views.SecureLoginView.as_view(),name='login'),
 path('logout/',LogoutView.as_view(),name='logout'),
 path('password/',PasswordChangeView.as_view(template_name='ledger/password.html',success_url='/password/done/'),name='password'),
 path('password/done/',PasswordChangeDoneView.as_view(template_name='ledger/password_done.html')),
 path('',views.dashboard,name='dashboard'),path('courses/',views.courses,name='courses'),
 path('analytics/',views.analytics,name='analytics'),
 path('courses/<int:pk>/',views.course_detail,name='course'),
 path('new/<str:entity>/',views.create,name='create'),path('edit/<str:entity>/<int:pk>/',views.create,name='edit'),
 path('register/<str:section>/',views.register,name='register'),
 path('periods/<int:pk>/',views.period_detail,name='period'),
 path('periods/<int:pk>/<str:action>/',views.period_action,name='period_action'),
 path('payments/<int:pk>/confirm/',views.payment_confirm,name='payment_confirm'),
 path('payments/<int:pk>/attachment/',views.attachment,name='attachment'),
 path('discrepancies/<int:pk>/review/',views.review_discrepancy,name='review_discrepancy'),
 path('sources/<int:pk>/',views.source_download,name='source'),
 path('history/<int:pk>/adjust/',views.adjust_history,name='adjust_history'),
 path('export/<str:kind>/',views.export,name='export'),
 path('export/table/<str:section>/<str:kind>/',views.register_export,name='register_export'),
 path('generate/<int:pk>/',views.generate_documents,name='generate'),
 path('documents/<int:pk>/',views.document_detail,name='document'),
 path('documents/<int:pk>/approve/',views.approve_document,name='approve_document'),
 path('documents/<int:pk>/file/<str:name>/',views.document_file,name='document_file'),
 path('google/',views.integration,name='integration'),path('google/connect/',google.connect,name='google_connect'),
 path('google/callback/',google.callback,name='google_callback'),path('google/disconnect/',google.disconnect,name='google_disconnect'),
 path('courses/<int:pk>/google/<str:action>/',google.course_google,name='course_google'),
]
