from __future__ import annotations
import argparse,csv,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))

QC_FIELDS=['record_id','run_index','video','cell','analysis','pipeline_status','qc_decision','track_preview','track_pdf','run_dir','notes','date_run','collected_at','replaces','replaced_by']
VALID_DECISIONS={'PENDING','APPROVED','DONE','RERUN','RERUNNING','SUPERSEDED'}

def read(path): return list(csv.DictReader(path.open(encoding='utf-8-sig'))) if path.exists() else []

def normalize_decision(value):
 value=(value or 'PENDING').strip().upper()
 if value=='FIXED': value='SUPERSEDED'
 return value if value in VALID_DECISIONS else 'PENDING'

def normalize_qc_row(raw):
 row={key:'' for key in QC_FIELDS}
 for key in QC_FIELDS:
  if key in raw and raw.get(key) is not None: row[key]=raw.get(key,'')
 row['pipeline_status']=row['pipeline_status'] or raw.get('status','') or raw.get('post','')
 row['qc_decision']=normalize_decision(row['qc_decision'] or raw.get('qc',''))
 row['track_preview']=row['track_preview'] or raw.get('track_map','')
 if not row['date_run']:
  row['date_run']=raw.get('run_timestamp','') or raw.get('collected_at','')
  if not row['date_run']:
   rid=row.get('record_id',''); stamp=rid.rsplit('_',1)[-1] if '_' in rid else ''
   if len(stamp)==15 and stamp[8]=='T' and stamp.replace('T','').isdigit():
    row['date_run']=f'{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}'
 return row

def write(path,rows,fields=QC_FIELDS):
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
 with tmp.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
 tmp.replace(path)


def annotate_run_summaries(run_dir, record_id, decision, notes):
 outputs=Path(run_dir)/'outputs'
 if not outputs.exists(): return 0
 changed=0
 for path in outputs.rglob('*.csv'):
  name=path.name.lower()
  if 'summary' not in name or 'dictionary' in name or 'manifest' in name or 'qc_review_bundle' in path.parts: continue
  rows=read(path)
  if not rows: continue
  for row in rows:
   row['qc_record_id']=record_id
   row['qc_decision']='APPROVED' if decision=='DONE' else decision
   row['qc_notes']=notes or ''
  fields=list(rows[0].keys())
  write(path,rows,fields)
  changed+=1
 return changed

def rebuild(root):
 qc=root/'QC'; rows=[normalize_qc_row(r) for r in read(qc/'run_status.csv')]; outdir=qc/'master_summaries';outdir.mkdir(parents=True,exist_ok=True)
 # Fast QC decisions defer per-run summary annotation. Rebuilding masters is the
 # synchronization point that applies the final QC metadata to all approved runs.
 for rec in rows:
  if rec.get('qc_decision') in ('APPROVED','DONE'):
   annotate_run_summaries(rec.get('run_dir',''),rec.get('record_id',''),rec.get('qc_decision','APPROVED'),rec.get('notes',''))
 counts={}
 for analysis,filename,candidates in [
  ('fight','master_fight_individual_summaries.csv',['combat_individual_summary_all.csv','fight_individuals_all.csv']),
  ('ba','master_BA_individual_summaries.csv',['ba_individual_summary_all.csv','BA_individuals_all.csv'])]:
  combined=[]
  for rec in rows:
   if rec.get('qc_decision') not in ('APPROVED','DONE') or rec.get('analysis')!=analysis:continue
   run=Path(rec['run_dir']);src=None
   for name in candidates:
    p=run/'outputs'/name
    if p.exists():src=p;break
   if not src:continue
   for n,data in enumerate(read(src),1):combined.append({'qc_record_id':rec['record_id'],'qc_decision':'APPROVED','qc_notes':rec.get('notes',''),'qc_source_file':str(src),'qc_source_row':n,**data})
  target=outdir/filename
  write(target,combined,list(combined[0]) if combined else ['qc_record_id','qc_decision','qc_source_file','qc_source_row'])
  counts[analysis]=len(combined)
 return counts

def same_subject(a,b):
 return all((a.get(k,'') or '').strip()==(b.get(k,'') or '').strip() for k in ('video','cell','analysis'))

def set_status(root,rid,decision,notes='',fast=False):
 decision=normalize_decision(decision); path=root/'QC'/'run_status.csv';rows=[normalize_qc_row(r) for r in read(path)]
 target=next((r for r in rows if r.get('record_id')==rid),None)
 if target is None:raise SystemExit(f'Unknown record ID: {rid}')
 target['qc_decision']=decision;target['notes']=notes if notes is not None else target.get('notes','')
 superseded=[]
 # Approving a replacement automatically retires older bad/in-progress runs for the same video/cell/analysis.
 if decision in ('APPROVED','DONE'):
  candidates=[r for r in rows if r is not target and same_subject(r,target) and r.get('qc_decision') in ('RERUN','RERUNNING')]
  candidates.sort(key=lambda r:(r.get('date_run',''),r.get('run_index','')))
  for old in candidates:
   old['qc_decision']='SUPERSEDED';old['replaced_by']=rid;superseded.append(old['record_id'])
  if superseded: target['replaces']=';'.join(superseded)
 write(path,rows)
 annotated=0
 counts={}
 if not fast:
  annotated=annotate_run_summaries(target.get('run_dir',''),rid,decision,target.get('notes',''))
  counts=rebuild(root)
 return {'decision':decision,'record_id':rid,'notes':target.get('notes',''),'summaries_annotated':annotated,'superseded':superseded,'masters':counts,'fast':bool(fast)}

def migrate_index(root):
 path=root/'QC'/'run_status.csv';rows=[normalize_qc_row(r) for r in read(path)]
 if rows or path.exists(): write(path,rows)
 return {'migrated_rows':len(rows),'path':str(path)}

def main():
 p=argparse.ArgumentParser();p.add_argument('--project-root',required=True);p.add_argument('--record-id');p.add_argument('--decision',choices=sorted(VALID_DECISIONS));p.add_argument('--notes',default=None);p.add_argument('--fast',action='store_true');p.add_argument('--rebuild',action='store_true');p.add_argument('--migrate-index',action='store_true');a=p.parse_args();root=Path(a.project_root)
 if a.migrate_index: print(migrate_index(root))
 elif a.rebuild: print(rebuild(root))
 else: print(set_status(root,a.record_id,a.decision,a.notes,fast=a.fast))
if __name__=='__main__':main()
