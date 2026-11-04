from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm, PrivacyForm, RegistrationForm


def login_view(request):
    if request.user.is_authenticated:
        return redirect('/')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data['phone']
            password = form.cleaned_data['password']
            user = authenticate(phone=phone, password=password)
            if user:
                login(request, user)
                return redirect('/')
            else:
                messages.error(request, "Try again")
        else:
            messages.error(request, "Phone number & Password doesn't match!")
    else:
        form = LoginForm()

    context = {"login_form": form}
    return render(request, 'login.html', context)


def logout_view(request):
    logout(request)
    return redirect('/')


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('/')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)

        if form.is_valid():
            phone = form.cleaned_data.get('phone')
            raw_password = form.cleaned_data.get('password1')
            form.save(commit=True)
            account = authenticate(phone=phone, password=raw_password)
            if account:
                login(request, account)
                return redirect('/')
            return redirect('login')

        context = {'registration_form': form}
    else:
        form = RegistrationForm()
        context = {"registration_form": form}

    return render(request, 'signup.html', context)


@login_required
def privacy_view(request):
    # Not `request.POST or None`: unchecking every box posts an empty dict,
    # which is falsy, and the opt-out would be silently ignored.
    form = PrivacyForm(
        request.POST if request.method == 'POST' else None,
        instance=request.user.profile,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Privacy settings saved.')
        return redirect('privacy')
    return render(request, 'privacy.html', {'form': form})
