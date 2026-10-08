"""Event-aligned equipment/quality association and lag analysis."""
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


def associations(rows):
    """Correlate five-second bucket RMS and inspected defect frequency only."""
    if not rows:
        return {'buckets':[],'pearson':None,'spearman':None,'lags':[],'sample_count':0}
    frame = pd.DataFrame(rows)
    frame = frame[frame['defective'].notna()].copy()
    frame['timestamp'] = pd.to_datetime(frame['timestamp'],utc=True,format='ISO8601')
    if frame.empty:
        return {'buckets':[],'pearson':None,'spearman':None,'lags':[],'sample_count':0}
    timeline = frame.set_index('timestamp').resample('5s').agg(rms=('vibration_x','mean'),defect_rate=('defective','mean'),n=('defective','count'))
    timeline.loc[timeline.n < 2,['rms','defect_rate']]=np.nan
    grouped = timeline.dropna()
    def coefficient(x,y,method):
        """Avoid undefined correlation for constant or insufficient samples."""
        if len(x)<4 or np.std(x)<1e-9 or np.std(y)<1e-9:
            return None
        return float(method(x,y)[0])
    lags = []
    for lag in range(0,7):
        pair = pd.concat([timeline.rms,timeline.defect_rate.shift(-lag)],axis=1).dropna()
        lags.append({'seconds':lag*5,'pearson':coefficient(pair.iloc[:,0],pair.iloc[:,1],pearsonr),'pairs':len(pair)})
    return {'buckets':[{'timestamp':time.isoformat(),'rms':float(row.rms),'defect_rate':float(row.defect_rate),'n':int(row.n)} for time,row in grouped.iterrows()],
            'pearson':coefficient(grouped.rms,grouped.defect_rate,pearsonr),
            'spearman':coefficient(grouped.rms,grouped.defect_rate,spearmanr),
            'lags':lags,'sample_count':len(frame),'bucket_count':len(grouped),
            'limitation':'Synthetic coupling; association alone does not establish real-world causality. Conveyor-stop periods are excluded.'}
