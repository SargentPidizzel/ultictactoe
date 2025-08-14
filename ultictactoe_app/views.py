from django.shortcuts import redirect, render
from django.views import View

# Create your views here.

class Game(View):
    def get(self, request, room_code):
        # room_code ist jetzt z. B. 3200
        return render(request, 'game.html', {"room_code": room_code})
    
    
class Index(View):
    def get(self, request):

        return render(request, 'index.html')
    
    

class Board(View):
    def get(self, request):
        # room_code ist jetzt z. B. 3200
        return render(request, 'game.html')