from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/create/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.UserUpdateView.as_view(), name='user_edit'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user_delete'),
    path('fix-admin-role/', views.fix_admin_role, name='fix_admin_role'),

    # Password Reset (admin-controlled)
    path('users/<int:pk>/send-reset/', views.send_reset_link, name='send_reset_link'),
    path('users/send-reset-all/', views.send_reset_link_all, name='send_reset_all'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('reset-password/<str:token>/', views.reset_password_form, name='reset_password'),
    path('reset-requests/', views.reset_requests_list, name='reset_requests'),
    path('reset-requests/<int:pk>/reject/', views.reject_reset, name='reject_reset'),
    path('settings/permissions/', views.permission_matrix, name='permission_matrix'),
    path('settings/permissions/toggle/', views.ajax_toggle_permission, name='toggle_permission'),
]
