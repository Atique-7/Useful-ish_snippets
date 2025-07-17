# BaseModelForm for HTMX autosave in Django
from django import forms
from django.urls import reverse


class BaseModelForm(forms.ModelForm):
    """
    A ModelForm subclass that wires up HTMX autosave on every non-hidden field.
    """

    app_label = forms.CharField(widget=forms.HiddenInput())
    model_name = forms.CharField(widget=forms.HiddenInput())
    object_id = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)

        # Hidden metadata for the save URL
        self.fields["app_label"].initial = self._meta.model._meta.app_label
        self.fields["model_name"].initial = self._meta.model._meta.model_name
        self.fields["object_id"].initial = str(getattr(self.instance, "pk", "") or "")

        save_url = reverse(
            "common:save",
            kwargs={
                "app_label": self.fields["app_label"].initial,
                "model_name": self.fields["model_name"].initial,
                "object_id": self.fields["object_id"].initial or 0,
            },
        )

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.HiddenInput):
                continue

            # choose trigger per widget type
            if isinstance(widget, forms.Select):
                trigger = "change"
            elif isinstance(widget, forms.CheckboxInput):
                trigger = "change"
            elif isinstance(widget, forms.Textarea):
                trigger = "blur"
            else:
                trigger = "change delay:500ms"

            widget.attrs.update(
                {
                    "hx-post": save_url,
                    "hx-trigger": trigger,
                    "hx-target": f"#field-{name}-container",
                    "hx-swap": "outerHTML",
                    "hx-include": "closest form",  # always send CSRF + metadata + field
                    "hx-indicator": ".saving-indicator",
                }
            )


# views.py
from django.apps import apps
from django.forms import modelform_factory
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from importlib import import_module


def lookup_form_class(form_class_path: str):
    """
    Given "myapp.forms.MyForm", import and return the class.
    Falls back to BaseModelForm on any error.
    """
    try:
        module_path, class_name = form_class_path.rsplit(".", 1)
        mod = import_module(module_path)
        return getattr(mod, class_name)
    except Exception:
        return BaseModelForm


@require_POST
def htmx_field_save(request, app_label, model_name, object_id):
    Model = apps.get_model(app_label, model_name)
    instance = get_object_or_404(Model, pk=object_id)

    # 1) Figure out which field fired
    field_name = request.headers.get("HX-Trigger-Name")
    if not field_name:
        excluded = {"csrfmiddlewaretoken", "app_label", "model_name", "object_id"}
        field_name = next((k for k in request.POST if k not in excluded), None)
    if not field_name:
        return HttpResponse(status=400)

    # 2) AUTO-DISCOVER the right BaseFormClass
    BaseFormClass = get_custom_modelform(app_label, Model)

    # 3) Build your single-field form subclass
    DynamicFormClass = modelform_factory(
        Model,
        form=BaseFormClass,
        fields=[field_name],
    )

    # 3) Instantiate and validate
    form = DynamicFormClass(request.POST, instance=instance)
    bound = form[field_name]

    # 3) Validate and SAVE just that single column
    if form.is_valid():
        # commit=False prevents saving all fields (some blank)
        obj = form.save(commit=False)
        # update_fields=['foo'] writes only that column
        obj.save(update_fields=[field_name])
        # Rebind to the saved instance so value() is the cleaned one
        form = DynamicFormClass(instance=obj)
        bound = form[field_name]
        css_class = "valid-value"
        status_code = 200
    else:
        status_code = 400
        css_class = "invalid-value"

    # 4) Render and return the one field’s container
    html = render_to_string(
        "common/partials/field_container.html",
        {
            "field": bound,
            "css_class": css_class,
            "errors": bound.errors,
            "help_text": bound.help_text,
        },
        request=request,
    )
    return HttpResponse(html)


# urls.py
from django.urls import path

app_name = "common"

urlpatterns = [
    path(
        "<str:app_label>/<str:model_name>/<int:object_id>/save/",
        htmx_field_save,
        name="save",
    ),
]


# Example usage in a view
# def record_edit(request, pk):
#     Model = apps.get_model("demo_fields", "DemoRecord")
#     inst = get_object_or_404(Model, pk=pk)
#     form = DemoRecordForm(request.POST or None, instance=inst)

#     if request.method == "POST" and "HX-Request" not in request.headers:
#         if form.is_valid():
#             form.save()
#             return redirect("demo_fields:record_edit", pk=pk)

#     # return render(request, "demo_fields/er.html", {"form": form})
#     return render(
#         request,
#         "demo_fields/er.html",
#         {
#             "form": form,
#             "action_url": request.path,  # or reverse("demo_fields:save", ...)
#         },
#     )
