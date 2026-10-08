"""Regression for PostgreSQL's optional fractional seconds in JSON exports."""
from factory.analytics import associations


def test_optional_fractional_seconds_and_utc_notations():
    """ISO times with and without fractions must retain the same real bucket axis."""
    rows=[]
    for bucket in range(4):
        for offset in [0,2]:
            suffix='.001Z' if offset==0 else '+00:00'
            rows.append({'timestamp':f'2026-10-08T00:00:{5*bucket+offset:02d}{suffix}',
                         'vibration_x':float(bucket+1),'defective':int(bucket>=2)})
    result=associations(rows)
    assert result['bucket_count']==4
    assert result['sample_count']==8
    assert result['pearson']>.8
