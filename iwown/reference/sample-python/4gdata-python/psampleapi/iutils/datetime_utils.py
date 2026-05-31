from datetime import datetime, timezone, timedelta

def parse_pb_datetime(seconds):
    try:
        # Convert seconds to a datetime object in UTC timezone
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        # Format the datetime object to the desired string format
        date_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        return date_str
    except Exception as e:
        return ""

def is_valid_date(date_str):
    """Validate if a string is a valid date in the format yyyy-MM-dd."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def get_previous_day(date_str):
    """Get the previous day of the given date in the format yyyy-MM-dd."""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        previous_day = date - timedelta(days=1)
        return previous_day.strftime("%Y-%m-%d")
    except ValueError:
        return ""
