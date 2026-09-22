from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render

from .forms import LoginForm, RegistrationForm


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
