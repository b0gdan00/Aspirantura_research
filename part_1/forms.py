from django import forms

from .models import Experiment


class ExperimentCreateForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ["title", "description", "color", "serial_port", "baud_rate"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Напр., Тест 1 / Серія A"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Коротко: умови, зразок, примітки..."}),
            "color": forms.TextInput(attrs={"type": "color", "class": "h-10 w-full cursor-pointer p-1 rounded-lg"}),
            "serial_port": forms.TextInput(attrs={"placeholder": "Напр., /dev/ttyUSB0 або COM3"}),
            "baud_rate": forms.NumberInput(attrs={"min": 1, "step": 1}),
        }

