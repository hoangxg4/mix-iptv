"""Pure EPG utility functions — no class dependencies."""
import re
from datetime import datetime, timedelta, timezone


def parse_xmltv_datetime(dt_str):
    """Parse XMLTV date format (YYYYMMDDHHMMSS ±HHMM) into a datetime.

    Also handles YYYYMMDDHHMMSS without timezone, or shorter variants
    (YYYYMMDDHHMM). Returns None on failure.
    """
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    # Strip timezone suffix (±HHMM) for parsing, save tz
    tz = None
    tz_match = re.search(r'([+-]\d{4})$', dt_str)
    if tz_match:
        tz_str = tz_match.group(1)
        dt_str = dt_str[:tz_match.start()].strip()
        # Convert ±HHMM to timedelta
        try:
            tz_hours = int(tz_str[1:3])
            tz_mins = int(tz_str[3:5])
            tz_delta = timedelta(hours=tz_hours, minutes=tz_mins)
            if tz_str[0] == '-':
                tz_delta = -tz_delta
            tz = timezone(tz_delta)
        except (ValueError, IndexError):
            pass

    # Pad with trailing zeros if shorter
    dt_str = dt_str.ljust(14, '0')
    try:
        dt = datetime.strptime(dt_str[:14], '%Y%m%d%H%M%S')
        if tz:
            dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        return None


def programme_in_window(prog, window_start, window_end):
    """Check if a programme's start time falls within the given window.

    Args:
        prog: XML <programme> element.
        window_start: datetime (naive or aware).
        window_end: datetime (naive or aware).
    """
    start_str = prog.get('start', '')
    dt = parse_xmltv_datetime(start_str)
    if dt is None:
        # Can't parse date — keep programme (safe default)
        return True
    # Make both sides naive for comparison if needed
    return window_start <= dt <= window_end


def dedup_programmes(programme_elements):
    """Deduplicate programme entries by (channel, start) tuple.

    Args:
        programme_elements: List of Element objects for <programme>.

    Returns:
        Deduplicated list with only the first occurrence of each (channel, start) pair.
    """
    seen = set()
    result = []
    for prog in programme_elements:
        ch = prog.get('channel', '')
        start = prog.get('start', '')
        key = (ch, start)
        if key not in seen:
            seen.add(key)
            result.append(prog)
    return result
