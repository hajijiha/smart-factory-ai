"""Verify simulator provenance, class counts, independent splits and image integrity."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from factory.common import DATA, CONFIG


def audit():
    """Check every generated image rather than trusting only a completion marker."""
    root=DATA/'vision'
    records=[json.loads(line) for line in (root/'manifest.jsonl').read_text().splitlines()]
    counts=Counter((row['scene']['split'],row['scene']['class_id']) for row in records)
    hashes=set()
    scenes=set()
    for row in records:
        image=root/row['image']
        assert hashlib.sha256(image.read_bytes()).hexdigest()==row['sha256'],f'Image integrity: {image}'
        assert row['scene']['scene_id'] not in scenes,'Duplicate scene'
        scenes.add(row['scene']['scene_id'])
        hashes.add(row['sha256'])
        split=row['scene']['split']
        label=root/'labels'/split/(image.stem+'.txt')
        values=label.read_text().split()
        if row['scene']['class_id']<0:
            assert not values
        else:
            assert len(values)==5 and int(values[0])==row['scene']['class_id']
            assert all(0<float(value)<1 for value in values[1:])
        assert row['joint']['velocity'][0]>1,'Missing physical motor baseline'
    assert len(records)==6000 and len(hashes)==len(records)
    assert sum(n for (_,cls),n in counts.items() if cls<0)==3000
    for cls in range(3):
        assert sum(n for (_,kind),n in counts.items() if kind==cls)==1000
    sensor=[json.loads(line) for line in (DATA/'sensor/provenance.jsonl').read_text().splitlines()]
    assert sum(row['level']==0 for row in sensor)==5000
    assert sum(row['level']>0 for row in sensor)==2000
    report={'passed':True,'vision_images':len(records),'unique_image_hashes':len(hashes),'sensor_windows':len(sensor),
            'sensor_normal':5000,'sensor_fault':2000,
            'vision_counts':{f'{split}/{cls}':n for (split,cls),n in sorted(counts.items())},
            'motor_rpm_range':[min(row['joint']['velocity'][0] for row in sensor)*60/(2*3.141592653589793),
                               max(row['joint']['velocity'][0] for row in sensor)*60/(2*3.141592653589793)],
            'domain_ranges':{key:[min(row['scene'][key] for row in records),max(row['scene'][key] for row in records)] for key in ['light','yaw']}}
    path=Path(CONFIG['paths']['reports'])/'dataset-audit.json'
    path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    audit()
