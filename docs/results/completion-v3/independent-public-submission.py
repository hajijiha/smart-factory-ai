"""Compare fetched public submission refs and runtime sources against the tested commit."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPORTS = Path(__file__).resolve().parent
REPO = REPORTS.parents[1] / 'smart-factory-ai'
TESTED = 'd200c86db86165e15b5daff4be2e4e6da0a71db6'
SUBMITTED = '56642e6a56fbecdb56e3e09b55b94d498ac9d4ab'
ARCHIVE = '1b4dfba66ee5ffaefb112ea4ab9eec80cf920339'


def git(*arguments):
    """Read fetched Git metadata without modifying checked-out source or remote refs."""
    return subprocess.check_output(['git', '-C', str(REPO), *arguments], text=True, encoding='utf-8').strip()


def commits(ref):
    """Keep the actual full SHA and subject of every reachable submitted commit."""
    return [{'sha':line.split('\t',1)[0], 'subject':line.split('\t',1)[1]} for line in git('log',ref,'--format=%H%x09%s').splitlines()]


def review():
    """Require public main, preserved archive, valid history and unchanged deployed runtime."""
    snapshot = json.loads((REPORTS/'public-repository-snapshot.json').read_text())
    anonymous = json.loads((REPORTS/'public-anonymous-metadata.json').read_text(encoding='utf-8-sig'))
    actual_refs = {name:git('rev-parse',name) for name in ('origin/main','origin/codex/complete-requirements','origin/archive/pre-completion-20261013')}
    history = commits('origin/main')
    convention = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*(?:\([^()]+\))?!?: .+')
    violations = [row for row in history if not convention.match(row['subject'])]
    archived_violations = [row for row in commits(ARCHIVE) if not convention.match(row['subject'])]
    core_paths = ['factory','simulator','compose.yaml','config.yaml','docker','requirements.txt','artifacts','.env.example']
    core_changes = git('diff','--name-status',TESTED,SUBMITTED,'--',*core_paths)
    all_changes = git('diff','--name-status',TESTED,SUBMITTED)
    model_blobs = {file:{'tested':git('rev-parse',TESTED+':'+file),'submitted':git('rev-parse',SUBMITTED+':'+file)}
                   for file in ('artifacts/pdm.pt','artifacts/vision.pt','artifacts/rul.pt','artifacts/drift-reference.json','artifacts/prognostics-model-card.json')}
    passed = snapshot['visibility']=='public' and snapshot['default_branch']=='main'
    passed &= anonymous['private'] is False and anonymous['visibility']=='public' and anonymous['default_branch']=='main'
    passed &= actual_refs['origin/main']==actual_refs['origin/codex/complete-requirements']==SUBMITTED
    passed &= actual_refs['origin/archive/pre-completion-20261013']==ARCHIVE
    passed &= not violations and not core_changes and all(value['tested']==value['submitted'] for value in model_blobs.values())
    return {'passed':bool(passed),'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'repository':snapshot,'anonymous_public_api_metadata':anonymous,'fetched_remote_refs':actual_refs,
            'verified_submitted_commit':SUBMITTED,'clean_startup_tested_commit':TESTED,
            'archive_preserved_commit':ARCHIVE,'main_history_commit_count':len(history),
            'main_history':history,'main_nonconventional_commits':violations,
            'archive_nonconventional_commits_preserved':archived_violations,
            'core_path_scope':core_paths,'core_runtime_source_changes':core_changes.splitlines(),
            'post_clean_test_changes':all_changes.splitlines(),'model_git_blob_bindings':model_blobs,
            'functional_regression_test_repeat_required':False,
            'reason_for_reusing_clean_startup':'All deployed factory/simulator/config/compose/Docker/dependency/model files are byte-identical between tested and submitted Git trees; later changes concern documentation, evidence rendering, quality scan scope and reproduction command.',
            'assessment_scope':'This verifies the fetched public main through exactly 56642e6. Later evidence-only commits are outside this snapshot and need their actual ref/subject/path diff checked before claiming a newer exact tip is verified.',
            'original_independent_report_preserved':True}


if __name__=='__main__':
    result=review()
    (REPORTS/'public-submission.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key not in ('main_history','post_clean_test_changes','model_git_blob_bindings')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
