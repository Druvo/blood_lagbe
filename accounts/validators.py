from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']

validate_image_extension = FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS)


def validate_image_size(image):
    """Reject oversized uploads. (ImageField itself already checks, with
    Pillow, that the file really is a decodable image.)"""
    if image.size > MAX_IMAGE_BYTES:
        raise ValidationError(
            'Pictures can be at most %(mb)d MB.',
            code='too_big', params={'mb': MAX_IMAGE_BYTES // (1024 * 1024)},
        )
