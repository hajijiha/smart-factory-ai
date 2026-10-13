"""Copy metric inputs outside timed loops during one frozen evaluator invocation."""
import copy
import json

from scripts import evaluate_live_factory as evaluator


selected_frames = []
captured = {}
call_counts = {'ap':0,'operating':0}
original_detection_metrics = evaluator.detection_metrics
original_operating_metrics = evaluator.operating_metrics


def remember(kind, selected, predictions, target_only):
    """Copy already produced metric inputs; never instrument detector or warmup calls."""
    if not selected_frames:
        selected_frames.extend(selected)
    names = (('onnx_ap_whole_frame','pytorch_ap_target_roi','onnx_ap_target_roi') if kind=='ap'
             else ('onnx_operating_whole_frame','pytorch_operating_target_roi','onnx_operating_target_roi'))
    index = call_counts[kind]
    assert index < 3 and target_only is (index > 0)
    captured[names[index]] = copy.deepcopy(predictions)
    call_counts[kind] += 1


def detection_metrics(selected, predictions, target_only=False):
    """Compute unchanged metrics, then copy existing arrays outside timed loops."""
    result = original_detection_metrics(selected,predictions,target_only)
    remember('ap',selected,predictions,target_only)
    return result


def operating_metrics(selected, predictions, target_only=True):
    """Compute unchanged operational metrics, then preserve existing arrays."""
    result = original_operating_metrics(selected,predictions,target_only)
    remember('operating',selected,predictions,target_only)
    return result


evaluator.detection_metrics = detection_metrics
evaluator.operating_metrics = operating_metrics
evaluator.main()
assert call_counts == {'ap':3,'operating':3} and len(selected_frames)==evaluator.SAMPLES
output = evaluator.REPORTS/'factory-web-v1'
rows = []
for index,item in enumerate(selected_frames):
    record_data = item['record']
    row = {key:record_data[key] for key in ('event_id','run_id','product_id','inspection_id',
                                           'sha256','ground_truth_class','product_roi')}
    row.update(index=index,model_version=item['quality']['model_version'],
               target_labels=item['target_labels'],whole_frame_labels=item['labels'])
    for name,predictions in captured.items():
        row[name] = predictions[index]
    rows.append(row)
assert len(rows) == evaluator.SAMPLES
path = output/'new-world-predictions.jsonl'
path.write_text(''.join(json.dumps(row,sort_keys=True,allow_nan=False)+'\n' for row in rows),encoding='utf-8')
report_path = output/'new-world-vision.json'
report = json.loads(report_path.read_text())
report['prediction_evidence'] = {'file':path.name,'frames':len(rows),
    'instrumentation':'External report driver copies detection_metrics/operating_metrics input arrays after metrics return; no detector, warmup, timed-loop or numerical changes.',
    'available_scopes':list(captured),'pytorch_whole_frame_scope':'Official aggregate validation metrics retained; raw PT whole-frame arrays are not passed to these metric helpers.',
    'sha256':evaluator.sha256_file(path)}
report_path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({'saved_predictions':str(path),'frames':len(rows)}),flush=True)
