import os

from django import forms

from .models import (ALLOWED_ATTACHMENT_EXTENSIONS,
                     MAX_ANNOUNCEMENT_ATTACHMENTS, MAX_ATTACHMENT_SIZE,
                     MAX_MESSAGE_LENGTH)


def validate_attachment(uploaded_file):
    """Extension allowlist + size cap. Applied to every file that enters the
    app, from either the direct-message or the announcement path."""
    name = getattr(uploaded_file, "name", "") or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise forms.ValidationError(f'"{name}" is not an allowed file type.')
    if uploaded_file.size > MAX_ATTACHMENT_SIZE:
        raise forms.ValidationError(f'"{name}" is too large (max 15 MB).')
    return uploaded_file


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's FileField handles one file; announcements accept several."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data if item]
        return [clean_one(data, initial)] if data else []


class DirectMessageForm(forms.Form):
    content = forms.CharField(required=False, max_length=MAX_MESSAGE_LENGTH)
    file = forms.FileField(required=False, validators=[validate_attachment])

    def clean_content(self):
        return (self.cleaned_data.get("content") or "").strip()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("content") and not cleaned.get("file"):
            raise forms.ValidationError("Nothing to send.")
        return cleaned


class AnnouncementForm(forms.Form):
    content = forms.CharField(
        required=False, max_length=MAX_MESSAGE_LENGTH, widget=forms.Textarea
    )
    files = MultipleFileField(required=False, validators=[validate_attachment])

    def clean_content(self):
        return (self.cleaned_data.get("content") or "").strip()

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        if len(files) > MAX_ANNOUNCEMENT_ATTACHMENTS:
            raise forms.ValidationError(
                f"At most {MAX_ANNOUNCEMENT_ATTACHMENTS} attachments per announcement."
            )
        return files

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("content") and not cleaned.get("files"):
            raise forms.ValidationError("An announcement needs text or an attachment.")
        return cleaned