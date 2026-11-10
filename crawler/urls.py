from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('ingia/', views.SiteLoginView.as_view(), name='login'),
    path('toka/', views.SiteLogoutView.as_view(), name='logout'),
    # Every account, readers included, can change its own password here; staff also have the admin's.
    path('akaunti/nenosiri/', auth_views.PasswordChangeView.as_view(), name='password_change'),
    path('akaunti/nenosiri/imebadilishwa/', auth_views.PasswordChangeDoneView.as_view(),
         name='password_change_done'),
    path('export.csv', views.export_csv, name='export_csv'),
    path('crawl/start/', views.start_crawl, name='start_crawl'),
    path('crawl/stop/', views.stop_crawl, name='stop_crawl'),
    path('crawl/status/', views.crawl_panel, name='crawl_panel'),
    path('articles/<int:pk>/', views.article_detail, name='article'),
    path('articles/<int:pk>/review/', views.review_article, name='review_article'),
]
