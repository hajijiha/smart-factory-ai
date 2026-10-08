"""Independent elapsed-time lag regression across missing inspection intervals."""
from datetime import datetime, timedelta, timezone
from factory.analytics import associations


def test_missing_bucket_preserves_actual_five_second_lag():
    """An uninspected five-second gap must not compress time in the lag analysis."""
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    rows = [
        {'timestamp':start+timedelta(seconds=5*bucket+.4*i),
         'vibration_x':float(bucket+1),'defective':int(i<bucket+1)}
        for bucket in [0,1,3,4,5,6] for i in range(10)
    ]
    result = associations(rows)
    assert result['bucket_count'] == 6
    lag = {row['seconds']:row for row in result['lags']}
    assert lag[0]['pairs'] == 6
    assert lag[5]['pairs'] == 4
    assert lag[5]['pearson'] is not None
