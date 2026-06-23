from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from .forms import CompanyDocumentForm
from .models import CompanyDocument


def _is_company_admin(user):
    return user.is_authenticated and (
        user.is_super_admin_user or user.is_admin_user)


class CompanyDocumentListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = CompanyDocument
    template_name = 'company/company_document_list.html'
    context_object_name = 'documents'
    paginate_by = 50

    def test_func(self):
        return _is_company_admin(self.request.user)

    def get_queryset(self):
        qs = CompanyDocument.objects.all()
        search = self.request.GET.get('search')
        doc_type = self.request.GET.get('type')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(issuing_authority__icontains=search) |
                Q(reference_number__icontains=search) |
                Q(custom_type__icontains=search))
        if doc_type:
            qs = qs.filter(document_type=doc_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['form'] = CompanyDocumentForm()
        ctx['type_choices'] = CompanyDocument.DOC_TYPE_CHOICES
        ctx['current_type'] = self.request.GET.get('type', '')
        ctx['search'] = self.request.GET.get('search', '')
        return ctx


@login_required
def company_document_upload(request):
    if not _is_company_admin(request.user):
        messages.error(request, 'Admin access required.')
        return redirect('company:document_list')
    if request.method == 'POST':
        form = CompanyDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, f'Document "{doc.title}" uploaded.')
        else:
            errs = '; '.join(f'{f}: {", ".join(e)}' for f, e in form.errors.items())
            messages.error(request, f'Could not upload document. {errs}')
    return redirect('company:document_list')


@login_required
def company_document_edit(request, pk):
    if not _is_company_admin(request.user):
        messages.error(request, 'Admin access required.')
        return redirect('company:document_list')
    doc = get_object_or_404(CompanyDocument, pk=pk)
    if request.method == 'POST':
        form = CompanyDocumentForm(request.POST, request.FILES, instance=doc)
        if form.is_valid():
            form.save()  # replacing the file reclaims the old one via pre_save signal
            messages.success(request, f'Document "{doc.title}" updated.')
            return redirect('company:document_list')
        messages.error(request, 'Please fix the errors below.')
    else:
        form = CompanyDocumentForm(instance=doc)
    return render(request, 'company/company_document_edit.html',
                  {'form': form, 'doc': doc})


@login_required
def company_document_delete(request, pk):
    if not _is_company_admin(request.user):
        messages.error(request, 'Admin access required.')
        return redirect('company:document_list')
    doc = get_object_or_404(CompanyDocument, pk=pk)
    doc.delete()  # file reclaimed by the central cleanup signal
    messages.success(request, 'Document deleted.')
    return redirect('company:document_list')
