import re


def validate_job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}", value):
        raise ValueError("job_id must be a 24-character lowercase hexadecimal MongoDB ID.")
    return value
