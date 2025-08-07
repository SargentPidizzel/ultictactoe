from django.shortcuts import redirect, render
from django.views import View

# Create your views here.

class Game(View):
    def get(self, request):

        return render(request, 'game.html')