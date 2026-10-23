#!/usr/bin/env python3
"""Deploy a hashed, explicit code release with drift detection and rollback.

Run on the target host after preparing a stage/manifest.json. No credentials,
recipient lists, output mail or send-ledger files belong in a release.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import py_compile
import shutil
import sys
import tempfile
import argparse
import os

SAFE_CONFIGS = {'selection_policy.json','legacy_selection_policy.json','event_editorial_policy.json','event_editorial_policy.priority.json','email_quality_policy.json'}
VALIDATION_PATTERNS = [
    'test_automatic_delivery.py',
    'test_email_quality_control.py',
    'test_event_editorial.py', 'test_editorial_value_v3.py', 'test_quiet_day_selection.py',
    'test_manual_news_editorial_approval.py', 'test_source_selection_refactor.py', 'test_source_release.py',
    'test_high_value_coverage.py', 'test_source_quality.py', 'test_source_selection_routing.py',
    'test_source_refresh_adapters.py', 'test_editorial_backlog.py',
    'test_discovery_coverage.py', 'test_news_budget.py',
    'test_event_source_followup.py', 'test_grouping_recovery.py', 'test_publication_priority.py',
    'test_daily_prose.py', 'test_research_prose_recovery.py', 'test_news_prose_recovery.py',
    'test_newspaper_sections.py', 'test_newspaper_text.py', 'test_newspaper_illustrated.py',
    'test_prod_prose_gate.py', 'test_approved_editorial_v2.py',
    'test_news_generation_integrity.py', 'test_source_history_aliases.py', 'test_source_freshness.py',
    'test_research_image_staging.py', 'test_holiday_auto_publish.py',
    'test_research_cache_reuse.py', 'test_research_manifest_coverage.py',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_path(root, relative):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='ai_daily_workflow':
        raise ValueError('Release path escapes workflow: '+relative)
    if len(path.parts)==2:
        allowed=path.name=='README.md'
    else:
        folder=path.parts[1]
        allowed=(folder in {'scripts','tests'} and path.suffix=='.py' or
                 folder=='tests' and len(path.parts) >= 4 and path.parts[2]=='fixtures' and path.suffix=='.json' or
                 folder in {'docs','prompts'} and path.suffix=='.md' or
                 folder=='config' and path.name in SAFE_CONFIGS or
                 path.as_posix()=='ai_daily_workflow/deploy/systemd/ai-daily-prepare.service')
    target=root/path
    if not allowed or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('File is outside code-release scope: '+relative)
    return target


def deploy(stage, root, validator=None):
    stage,root=Path(stage),Path(root)
    manifest=json.loads((stage/'manifest.json').read_text())
    files=manifest['files'];base=manifest['base_sha256']
    if not files or set(files)!=set(base):raise ValueError('Incomplete release manifest')
    for rel,expected in files.items():
        src,dst=release_path(stage,rel),release_path(root,rel)
        if sha(src)!=expected:raise ValueError('Stage checksum mismatch: '+rel)
        if (sha(dst) if dst.exists() else None)!=base[rel]:raise ValueError('Target drift detected: '+rel)
    backup=root/'deploy_backups'/('source_release_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True)
    shutil.copy2(stage/'manifest.json',backup/'manifest.json')
    changed=[]
    try:
        for rel in files:
            dst=release_path(root,rel);old=backup/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,old)
            fd,temp=tempfile.mkstemp(prefix=dst.name+'.release-',dir=dst.parent);os.close(fd)
            try:shutil.copy2(stage/rel,temp);os.replace(temp,dst)
            finally:
                if Path(temp).exists():Path(temp).unlink()
            changed.append(rel)
        for rel in files:
            dst=root/rel
            if dst.suffix=='.py':py_compile.compile(str(dst),doraise=True)
            if sha(dst)!=files[rel]:raise ValueError('Post-install checksum mismatch: '+rel)
        validation=validator(root) if validator else []
        record={'status':'deployed','version':manifest['version'],'backup':str(backup),'files':files,
                'manifest_sha256':sha(stage/'manifest.json'),'validation':validation}
        (backup/'deployment_result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        return record
    except BaseException:
        for rel in reversed(changed):
            dst=root/rel;old=backup/rel
            if old.exists():shutil.copy2(old,dst)
            elif dst.exists():dst.unlink()
        (backup/'rollback.json').write_text(json.dumps({'status':'rolled_back','files':changed})+'\n')
        raise


def validate_runtime(root):
    import subprocess
    result=[]
    for pattern in VALIDATION_PATTERNS:
        if not (root/'ai_daily_workflow/tests'/pattern).is_file():
            raise RuntimeError('Required release test is missing: '+pattern)
        p=subprocess.run([sys.executable,'-m','unittest','discover','-s','ai_daily_workflow/tests','-p',pattern,'-q'],
                         cwd=root,text=True,capture_output=True)
        result.append({'pattern':pattern,'exit_code':p.returncode,'output':p.stdout+p.stderr})
        if p.returncode:raise RuntimeError('Release validation failed: '+pattern+'\n'+p.stdout+p.stderr)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',required=True,type=Path);p.add_argument('--root',required=True,type=Path)
    args=p.parse_args();print(json.dumps(deploy(args.stage,args.root,validate_runtime),ensure_ascii=False))

if __name__=='__main__':main()
