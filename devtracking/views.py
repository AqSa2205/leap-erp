from django.contrib.auth.decorators import login_required
from django.shortcuts import render


# Placeholder stubs so the nav `{% url %}` tags resolve. Task 3/4 replace these
# with the real dashboard and my-tasks views.
@login_required
def dashboard(request):
    return render(request, 'devtracking/coming_soon.html', {'title': 'Dev Tracking'})


@login_required
def my_tasks(request):
    return render(request, 'devtracking/coming_soon.html', {'title': 'My Tasks'})
