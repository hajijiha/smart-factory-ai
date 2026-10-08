"""Verify missing inspections cannot masquerade as normal quality observations."""
from datetime import datetime,timedelta,timezone
from factory.analytics import associations


def test_missing_inspection_excluded():
    """Uninspected stopped-line events must not dilute the measured defect frequency."""
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[{'timestamp':start+timedelta(seconds=i),'vibration_x':float(i),
           'defective':None if i==3 else int(i>=10)} for i in range(30)]
    result=associations(rows)
    assert result['sample_count']==29
    assert result['pearson']>.7


def test_constant_series_has_no_fabricated_correlation():
    """Pearson/Spearman are undefined when no actual variation is observed."""
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[{'timestamp':start+timedelta(seconds=i),'vibration_x':.1,'defective':0} for i in range(30)]
    result=associations(rows)
    assert result['pearson'] is None
