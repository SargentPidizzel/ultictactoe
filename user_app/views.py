from django.views import View
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()  # <— WICHTIG: benötigst du für User.objects[...]

class Login(View):
    def get(self, request):
        return render(request, "login.html")

    def post(self, request):
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        remember = request.POST.get("remember")  # optional: "Angemeldet bleiben" Checkbox

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not user.is_active:
                messages.error(request, "Dieses Konto ist deaktiviert.")
                print("Login")
                return render(request, "login.html")

            login(request, user)

            # Session-Lebensdauer steuern, falls du die Checkbox nutzt
            if remember:
                # z. B. 30 Tage
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                # Logout bei Browser-Schließen
                request.session.set_expiry(0)

            messages.success(request, f"Willkommen zurück, {user.get_username()}!")
            next_url = request.GET.get("next") or "home"
            return redirect(next_url)

        messages.error(request, "Anmeldung fehlgeschlagen. Bitte Daten prüfen.")
        return render(request, "login.html")


class Register(View):
    def get(self, request):
        return render(request, "register.html")

    def post(self, request):
        username  = (request.POST.get("username") or "").strip()
        email     = (request.POST.get("email") or "").strip()      # <- wichtig: niemals None
        password1 = request.POST.get("password1") or ""
        password2 = request.POST.get("password2") or ""

        if not username or not password1 or not password2:
            messages.error(request, "Bitte alle Pflichtfelder ausfüllen.")
            return render(request, "register.html")

        if password1 != password2:
            messages.error(request, "Die Passwörter stimmen nicht überein.")
            return render(request, "register.html")

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "Dieser Benutzername ist bereits vergeben.")
            return render(request, "register.html")

        # optional: E-Mail prüfen, falls du Einzigartigkeit möchtest
        if email and User.objects.filter(email__iexact=email).exists():
            messages.error(request, "Diese E-Mail wird bereits verwendet.")
            return render(request, "register.html")

        try:
            validate_password(password1)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return render(request, "register.html")

        # Empfohlen: create_user
        user = User.objects.create_user(username=username, password=password1, email=email)

        login(request, user)
        messages.success(request, f"Willkommen, {user.get_username()}!")
        return render(request, "index.html")

