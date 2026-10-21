"""Raw RealSense metadata diagnostics; never produces exposure-time evidence."""
import math


METADATA_FIELDS = ('frame_timestamp', 'sensor_timestamp', 'actual_exposure', 'auto_exposure')


def frame_diagnostics(rs, frame):
    """Report SDK values without guessing timestamp domains or exposure units."""
    result = dict(metadata_raw={}, missing_metadata=[], errors=[])
    for key, read in (('frame_number', frame.get_frame_number),
                      ('timestamp_ms', frame.get_timestamp),
                      ('timestamp_domain', lambda: str(frame.get_frame_timestamp_domain()))):
        try:
            value = read()
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError('non-finite SDK value')
            result[key] = value
        except (RuntimeError, ValueError) as exc:
            result['errors'].append(f'{key}: {exc}')
    for key in METADATA_FIELDS:
        enum = getattr(rs.frame_metadata_value, key, None)
        try:
            if enum is None or not frame.supports_frame_metadata(enum):
                result['missing_metadata'].append(key)
            else:
                result['metadata_raw'][key] = int(frame.get_frame_metadata(enum))
        except (RuntimeError, ValueError) as exc:
            result['errors'].append(f'{key}: {exc}')
    return result
