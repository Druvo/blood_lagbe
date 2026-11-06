"""Trust signals: verified hospitals."""
from growth.models import VerifiedHospital


def _norm(text):
    return ' '.join((text or '').split()).casefold()


def verified_hospital_for(blood_request):
    """The VerifiedHospital a request points at, or None.

    A match is the same listed place, or the same name (ignoring case and
    extra spaces) in the same district. Staff manage the list in the admin.
    """
    if blood_request is None or not blood_request.district:
        return None
    candidates = VerifiedHospital.objects.filter(
        district=blood_request.district,
    )
    wanted = _norm(blood_request.hospital)
    for hospital in candidates:
        if (
            blood_request.place_id
            and hospital.place_id == blood_request.place_id
        ):
            return hospital
        if wanted and _norm(hospital.name) == wanted:
            return hospital
    return None


def is_verified_hospital(blood_request):
    return verified_hospital_for(blood_request) is not None
