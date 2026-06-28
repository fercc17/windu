"""Forms for maintenance windows."""
from django import forms

_DT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]


class MaintenanceWindowForm(forms.Form):
    starts_at = forms.DateTimeField(
        input_formats=_DT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=_DT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    notify_pagerduty = forms.BooleanField(required=False, initial=True, label="PagerDuty silence")
    notify_mattermost = forms.BooleanField(required=False, initial=True, label="Mattermost DM")
    notify_email = forms.BooleanField(required=False, label="Email CIA owners")

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            raise forms.ValidationError("End time must be after start time.")
        return cleaned

    def selected_channels(self) -> set[str]:
        cd = self.cleaned_data
        channels = set()
        if cd.get("notify_pagerduty"):
            channels.add("pagerduty")
        if cd.get("notify_mattermost"):
            channels.add("mattermost")
        if cd.get("notify_email"):
            channels.add("email")
        return channels
